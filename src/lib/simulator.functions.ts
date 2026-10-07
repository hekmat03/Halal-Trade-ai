import { createServerFn } from "@tanstack/react-start";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";
import { z } from "zod";
import type { TablesUpdate } from "@/integrations/supabase/types";
import {
  QUALIFICATION_KEYS,
  askQuestion,
  detectHumanAttentionNeeded,
  detectUrgency,
  getTemplate,
  openingMessage,
  qualifiedMessage,
  type AiTone,
  type QualificationKey,
} from "@/lib/industry-templates";

const BusinessScope = z.object({ businessId: z.string().uuid() });

const LeadScope = z.object({
  leadId: z.string().uuid(),
  businessId: z.string().uuid(),
});

const FaqSchema = z.array(z.object({ q: z.string().max(300), a: z.string().max(1000) })).max(30);

const AiConfigInput = z.object({
  businessId: z.string().uuid(),
  name: z.string().trim().min(1).max(120),
  industry: z.string().trim().max(60),
  serviceArea: z.string().trim().max(200).nullable(),
  phone: z.string().trim().max(40).nullable(),
  businessHours: z.string().trim().max(200).nullable(),
  description: z.string().trim().max(2000).nullable(),
  servicesOffered: z.string().trim().max(2000).nullable(),
  faqs: FaqSchema,
  aiTone: z.enum(["professional", "friendly", "casual"]),
});

export const getAiConfig = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => BusinessScope.parse(input))
  .handler(async ({ context, data }) => {
    const { data: biz, error } = await context.supabase
      .from("businesses")
      .select(
        "id, company_name, industry, phone_number, service_area, business_hours, description, services_offered, faqs, ai_tone",
      )
      .eq("id", data.businessId)
      .maybeSingle();
    if (error) throw error;
    if (!biz) return null;
    return {
      id: biz.id,
      name: biz.company_name,
      industry: biz.industry ?? "roofing",
      phone: biz.phone_number,
      serviceArea: biz.service_area,
      businessHours: biz.business_hours,
      description: biz.description,
      servicesOffered: biz.services_offered,
      faqs: (Array.isArray(biz.faqs) ? biz.faqs : []) as { q: string; a: string }[],
      aiTone: (biz.ai_tone ?? "professional") as AiTone,
    };
  });

export const updateAiConfig = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => AiConfigInput.parse(input))
  .handler(async ({ context, data }) => {
    const { error } = await context.supabase
      .from("businesses")
      .update({
        company_name: data.name,
        industry: data.industry,
        phone_number: data.phone,
        service_area: data.serviceArea,
        business_hours: data.businessHours,
        description: data.description,
        services_offered: data.servicesOffered,
        faqs: data.faqs,
        ai_tone: data.aiTone,
      })
      .eq("id", data.businessId);
    if (error) throw error;
    return { ok: true };
  });

type Qualification = Record<string, string> & { _pending?: string };

function readQualification(value: unknown): Qualification {
  return value && typeof value === "object" && !Array.isArray(value)
    ? ({ ...(value as Record<string, string>) } as Qualification)
    : ({} as Qualification);
}

function nextKey(q: Qualification): QualificationKey | null {
  return QUALIFICATION_KEYS.find((k) => !q[k]) ?? null;
}

async function loadConfig(
  supabase: { from: (t: string) => any },
  businessId: string,
) {
  const { data: biz } = await supabase
    .from("businesses")
    .select("company_name, industry, ai_tone, service_area")
    .eq("id", businessId)
    .maybeSingle();
  const tone = ((biz?.ai_tone as string) ?? "professional") as AiTone;
  return {
    companyName: (biz?.company_name as string) ?? "our team",
    tone,
    serviceArea: (biz?.service_area as string) ?? null,
    template: getTemplate(biz?.industry as string | null),
  };
}

/** Auto-generate the opening AI message for a new lead (idempotent). */
export const startAiFollowUp = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => LeadScope.parse(input))
  .handler(async ({ context, data }) => {
    const { count } = await context.supabase
      .from("conversations")
      .select("id", { count: "exact", head: true })
      .eq("lead_id", data.leadId);
    if ((count ?? 0) > 0) return { created: false as const };

    const cfg = await loadConfig(context.supabase as never, data.businessId);
    const { error } = await context.supabase.from("conversations").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      sender: "ai" as const,
      message: openingMessage({
        companyName: cfg.companyName,
        tone: cfg.tone,
        serviceArea: cfg.serviceArea,
      }),
    });
    if (error) throw error;

    await context.supabase.from("lead_activities").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      kind: "ai_followup",
      note: "AI sent the opening follow-up message",
    });
    return { created: true as const };
  });

const AdvanceInput = LeadScope.extend({
  customerMessage: z.string().trim().min(1).max(1000),
});

/**
 * Simulated qualification flow: store the customer reply, record it against the
 * question currently pending, then ask the next qualifying question — one at a
 * time. When all five are answered the answers are saved onto the lead record
 * and the lead is marked "Qualified". No external API calls.
 */
export const advanceQualification = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => AdvanceInput.parse(input))
  .handler(async ({ context, data }) => {
    const { data: lead, error: leadError } = await context.supabase
      .from("leads")
      .select("qualification, status, estimated_value, notes, urgency, needs_attention")
      .eq("id", data.leadId)
      .single();
    if (leadError) throw leadError;

    const cfg = await loadConfig(context.supabase as never, data.businessId);

    const { error: custError } = await context.supabase.from("conversations").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      sender: "customer" as const,
      message: data.customerMessage,
    });
    if (custError) throw custError;

    const q = readQualification(lead.qualification);
    const pending = q._pending as QualificationKey | undefined;
    if (pending) {
      q[pending] = data.customerMessage;
      delete q._pending;
    } else if (!q["initial_request"]) {
      q["initial_request"] = data.customerMessage;
    }

    const next = nextKey(q);
    let reply: string;
    let becameQualified = false;

    if (next) {
      q._pending = next;
      reply = askQuestion(cfg.tone, cfg.template.qualificationQuestions[next]);
    } else {
      reply = qualifiedMessage(cfg.tone, cfg.companyName);
      becameQualified = true;
    }

    // Simulated urgency + human-attention detection, evaluated on every reply.
    const isUrgent = lead.urgency === "urgent" || detectUrgency(data.customerMessage);
    const attentionReason =
      detectHumanAttentionNeeded(data.customerMessage) ??
      (becameQualified ? "Qualification complete — ready to book" : null);
    const needsAttention = Boolean(lead.needs_attention) || Boolean(attentionReason);

    const update: TablesUpdate<"leads"> = {
      qualification: q,
      urgency: isUrgent ? "urgent" : "normal",
      needs_attention: needsAttention,
      attention_reason: needsAttention ? attentionReason ?? undefined : null,
      follow_up_stage: 0, // any real customer reply resets the "no response" reminder clock
      last_customer_reply_at: new Date().toISOString(),
    };

    if (becameQualified) {
      const summary = QUALIFICATION_KEYS.map((k) => `${k}: ${q[k]}`).join(" | ");
      const stamp = new Date().toISOString();
      const entry = `[${stamp}] AI qualification complete — ${summary}`;
      update["notes"] = lead.notes ? `${lead.notes}\n${entry}` : entry;
      if (lead.status === "new" || lead.status === "contacted") update["status"] = "qualified";
      if (!lead.estimated_value) update["estimated_value"] = cfg.template.typicalValue;
    } else if (lead.status === "new") {
      update["status"] = "contacted";
    }

    const { error: updError } = await context.supabase
      .from("leads")
      .update(update)
      .eq("id", data.leadId);
    if (updError) throw updError;

    const { data: aiMsg, error: aiError } = await context.supabase
      .from("conversations")
      .insert({
        lead_id: data.leadId,
        business_id: data.businessId,
        sender: "ai" as const,
        message: reply,
      })
      .select("id, sender, message, created_at")
      .single();
    if (aiError) throw aiError;

    if (becameQualified) {
      await context.supabase.from("lead_activities").insert([
        {
          lead_id: data.leadId,
          business_id: data.businessId,
          kind: "qualified",
          note: "AI collected all qualifying answers and saved them to the lead",
        },
        {
          lead_id: data.leadId,
          business_id: data.businessId,
          kind: "status_change",
          from_status: lead.status,
          to_status: "qualified",
        },
      ]);
    } else {
      await context.supabase.from("lead_activities").insert({
        lead_id: data.leadId,
        business_id: data.businessId,
        kind: "ai_followup",
        note: `AI asked: ${reply}`,
      });
    }

    // Only log a new urgency/attention activity the moment the flag turns on,
    // so the timeline doesn't repeat the same note on every later reply.
    if (isUrgent && lead.urgency !== "urgent") {
      await context.supabase.from("lead_activities").insert({
        lead_id: data.leadId,
        business_id: data.businessId,
        kind: "urgent_flagged",
        note: "Marked urgent — keywords suggesting active damage or an emergency were detected",
      });
    }
    if (needsAttention && !lead.needs_attention) {
      await context.supabase.from("lead_activities").insert({
        lead_id: data.leadId,
        business_id: data.businessId,
        kind: "needs_attention",
        note: attentionReason ?? "Flagged for human attention",
      });
    }

    if (becameQualified) {
      const { count: openAppt } = await context.supabase
        .from("appointments")
        .select("id", { count: "exact", head: true })
        .eq("lead_id", data.leadId)
        .in("status", ["requested", "scheduled"]);
      if ((openAppt ?? 0) === 0) {
        const { data: leadContact } = await context.supabase
          .from("leads")
          .select("customer_name, customer_phone")
          .eq("id", data.leadId)
          .single();
        const { data: appt } = await context.supabase
          .from("appointments")
          .insert({
            business_id: data.businessId,
            lead_id: data.leadId,
            customer_name: leadContact?.customer_name ?? null,
            customer_phone: leadContact?.customer_phone ?? null,
            notes: "Auto-created once AI qualification completed",
            status: "requested",
          })
          .select("id")
          .single();
        if (appt) {
          await context.supabase.from("notifications").insert({
            business_id: data.businessId,
            lead_id: data.leadId,
            type: "appointment_opportunity",
            title: "New appointment to confirm",
            body: `${leadContact?.customer_name ?? "A customer"} is ready to book — confirm a time.`,
            link: `/leads/${data.leadId}`,
          });
          await context.supabase.from("lead_activities").insert({
            lead_id: data.leadId,
            business_id: data.businessId,
            kind: "appointment_requested",
            note: "Appointment request created from qualified lead",
          });
        }
      }
    }

    return { reply: aiMsg, qualified: becameQualified, urgent: isUrgent, needsAttention, pending: next };
  });

const ReminderInput = LeadScope.extend({
  hoursSinceLastReply: z.number().min(0).max(24 * 30),
});

/**
 * Simulated "no reply" follow-up reminder. In production this would be driven
 * by a real scheduled job checking real elapsed time; for now the caller
 * supplies the simulated elapsed hours so the UI can trigger it on demand
 * (e.g. a "Simulate time passing" button) without a real backend cron job.
 *
 * Stage 1: first reminder after the configured wait.
 * Stage 2: second reminder after a further wait.
 * Stage 3: gives up automating and flags the lead for manual follow-up.
 */
export const sendFollowUpReminder = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => ReminderInput.parse(input))
  .handler(async ({ context, data }) => {
    const { data: lead, error: leadError } = await context.supabase
      .from("leads")
      .select("status, follow_up_stage, needs_attention")
      .eq("id", data.leadId)
      .single();
    if (leadError) throw leadError;

    if (lead.status === "qualified" || lead.status === "closed" || lead.status === "lost") {
      return { sent: false as const, reason: "Lead is no longer awaiting a reply" };
    }

    const cfg = await loadConfig(context.supabase as never, data.businessId);
    const stage = (lead.follow_up_stage ?? 0) + 1;

    if (stage > 2) {
      await context.supabase
        .from("leads")
        .update({ follow_up_stage: 3, needs_attention: true, attention_reason: "No response after two automated reminders" })
        .eq("id", data.leadId);
      await context.supabase.from("lead_activities").insert({
        lead_id: data.leadId,
        business_id: data.businessId,
        kind: "needs_attention",
        note: "No response after two automated reminders — needs manual follow-up",
      });
      return { sent: false as const, reason: "Escalated to manual follow-up" };
    }

    const reminderMessage =
      stage === 1
        ? `Hi again — just checking in from ${cfg.companyName}. Still want to get this sorted for you? Reply anytime.`
        : `Hi, following up once more from ${cfg.companyName} — happy to help whenever you're ready, just reply here.`;

    const { error: msgError } = await context.supabase.from("conversations").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      sender: "ai",
      message: reminderMessage,
    });
    if (msgError) throw msgError;

    await context.supabase.from("leads").update({ follow_up_stage: stage }).eq("id", data.leadId);

    await context.supabase.from("lead_activities").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      kind: "ai_followup",
      note: `Automated reminder #${stage} sent after no reply`,
    });

    return { sent: true as const, stage };
  });

/** One-click demo scenario, tagged is_demo and scoped to this business. */
export const generateDemoScenario = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => BusinessScope.parse(input))
  .handler(async ({ context, data }) => {
    const cfg = await loadConfig(context.supabase as never, data.businessId);
    const t = cfg.template;
    const now = Date.now();
    const at = (minsAgo: number) => new Date(now - minsAgo * 60_000).toISOString();

    const persona = {
      name: "Taylor Morgan (DEMO)",
      phone: "(555) 012-8899",
      email: "taylor.demo@example.com",
    };

    const answers: Record<string, string> = {
      initial_request: `Hi, I need help — ${t.commonQuestions[0].toLowerCase()}`,
      property_location: "142 Maple Ridge Dr, north side of town",
      issue_type: t.label === "Dental" ? "Pain, upper right molar" : `${t.label} issue — urgent`,
      damage_description: "Started yesterday and it's getting worse; visible damage in one spot",
      timeline: "As soon as possible — ideally tomorrow morning",
      insurance: "Yes, planning to file a claim",
    };

    const { data: call, error: callError } = await context.supabase
      .from("missed_calls")
      .insert({
        business_id: data.businessId,
        customer_name: persona.name,
        customer_phone: persona.phone,
        date_time: at(45),
        source: "phone",
        status: "qualified",
        notes: "DEMO DATA — sample missed call generated by Demo Mode",
        is_demo: true,
      })
      .select("id")
      .single();
    if (callError) throw callError;

    const { data: lead, error: leadError } = await context.supabase
      .from("leads")
      .insert({
        business_id: data.businessId,
        customer_name: persona.name,
        customer_phone: persona.phone,
        customer_email: persona.email,
        lead_source: "missed_call" as const,
        status: "qualified" as const,
        estimated_value: t.typicalValue,
        qualification: answers,
        notes: `[${new Date(now - 40 * 60_000).toISOString()}] DEMO DATA — AI qualification complete via Demo Mode`,
        is_demo: true,
      })
      .select("id")
      .single();
    if (leadError) throw leadError;

    const script: { sender: "ai" | "customer"; message: string; mins: number }[] = [
      { sender: "ai", message: openingMessage({ companyName: cfg.companyName, tone: cfg.tone, serviceArea: cfg.serviceArea }), mins: 44 },
      { sender: "customer", message: answers["initial_request"], mins: 43 },
    ];
    let m = 42;
    for (const key of QUALIFICATION_KEYS) {
      script.push({ sender: "ai", message: askQuestion(cfg.tone, t.qualificationQuestions[key]), mins: m-- });
      script.push({ sender: "customer", message: answers[key], mins: m-- });
    }
    script.push({ sender: "ai", message: qualifiedMessage(cfg.tone, cfg.companyName), mins: m });

    const { error: convError } = await context.supabase.from("conversations").insert(
      script.map((s) => ({
        lead_id: lead.id,
        business_id: data.businessId,
        sender: s.sender,
        message: s.message,
        created_at: at(s.mins),
        is_demo: true,
      })),
    );
    if (convError) throw convError;

    await context.supabase.from("lead_activities").insert([
      {
        lead_id: lead.id,
        business_id: data.businessId,
        kind: "created",
        to_status: "new",
        note: "DEMO DATA — lead created from sample missed call",
        is_demo: true,
      },
      {
        lead_id: lead.id,
        business_id: data.businessId,
        kind: "qualified",
        note: "DEMO DATA — AI collected all qualifying answers",
        is_demo: true,
      },
      {
        lead_id: lead.id,
        business_id: data.businessId,
        kind: "status_change",
        from_status: "new",
        to_status: "qualified",
        note: "DEMO DATA",
        is_demo: true,
      },
    ]);

    return { leadId: lead.id, missedCallId: call.id };
  });

export const resetDemoData = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => BusinessScope.parse(input))
  .handler(async ({ context, data }) => {
    await context.supabase
      .from("conversations")
      .delete()
      .eq("business_id", data.businessId)
      .eq("is_demo", true);
    await context.supabase
      .from("lead_activities")
      .delete()
      .eq("business_id", data.businessId)
      .eq("is_demo", true);
    await context.supabase.from("leads").delete().eq("business_id", data.businessId).eq("is_demo", true);
    await context.supabase
      .from("missed_calls")
      .delete()
      .eq("business_id", data.businessId)
      .eq("is_demo", true);
    return { ok: true };
  });

export const countDemoData = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => BusinessScope.parse(input))
  .handler(async ({ context, data }) => {
    const { count } = await context.supabase
    
