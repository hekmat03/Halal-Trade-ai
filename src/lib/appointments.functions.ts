import { createServerFn } from "@tanstack/react-start";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";
import { z } from "zod";

// Appointments: simulated booking flow. No real calendar/SMS confirmation yet —
// this stage is about the app closing the loop past "qualified" into a
// requested/scheduled appointment record, which is the differentiator over
// competitors that stop at "qualified lead."

const BusinessScope = z.object({ businessId: z.string().uuid() });

export const listAppointments = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => BusinessScope.parse(input))
  .handler(async ({ context, data }) => {
    const { data: rows, error } = await context.supabase
      .from("appointments")
      .select("id, lead_id, customer_name, customer_phone, preferred_at, notes, status, created_at")
      .eq("business_id", data.businessId)
      .order("created_at", { ascending: false });
    if (error) throw error;
    return rows ?? [];
  });

const CreateFromLeadInput = z.object({
  leadId: z.string().uuid(),
  businessId: z.string().uuid(),
  preferredAt: z.string().nullable().optional(),
  notes: z.string().trim().max(1000).optional(),
});

/**
 * Called automatically once a lead is fully qualified (see simulator.functions.ts),
 * or manually from the Lead Detail page. Creates a "requested" appointment plus
 * an appointment_opportunity notification for the owner. Idempotent per lead —
 * won't create a duplicate open appointment for the same lead.
 */
export const requestAppointmentFromLead = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => CreateFromLeadInput.parse(input))
  .handler(async ({ context, data }) => {
    const { count } = await context.supabase
      .from("appointments")
      .select("id", { count: "exact", head: true })
      .eq("lead_id", data.leadId)
      .in("status", ["requested", "scheduled"]);
    if ((count ?? 0) > 0) return { created: false as const };

    const { data: lead, error: leadError } = await context.supabase
      .from("leads")
      .select("customer_name, customer_phone")
      .eq("id", data.leadId)
      .single();
    if (leadError) throw leadError;

    const { data: appt, error } = await context.supabase
      .from("appointments")
      .insert({
        business_id: data.businessId,
        lead_id: data.leadId,
        customer_name: lead.customer_name,
        customer_phone: lead.customer_phone,
        preferred_at: data.preferredAt ?? null,
        notes: data.notes ?? "Auto-created once AI qualification completed",
        status: "requested",
      })
      .select("id")
      .single();
    if (error) throw error;

    await context.supabase.from("notifications").insert({
      business_id: data.businessId,
      lead_id: data.leadId,
      type: "appointment_opportunity",
      title: "New appointment to confirm",
      body: `${lead.customer_name ?? "A customer"} is ready to book — confirm a time.`,
      link: `/leads/${data.leadId}`,
    });

    await context.supabase.from("lead_activities").insert({
      lead_id: data.leadId,
      business_id: data.businessId,
      kind: "appointment_requested",
      note: "Appointment request created from qualified lead",
    });

    return { created: true as const, appointmentId: appt.id };
  });

const ConfirmInput = z.object({
  appointmentId: z.string().uuid(),
  businessId: z.string().uuid(),
  preferredAt: z.string(),
});

/** Owner (or, later, the customer via real SMS) picks/confirms a time. */
export const confirmAppointment = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => ConfirmInput.parse(input))
  .handler(async ({ context, data }) => {
    const { data: appt, error } = await context.supabase
      .from("appointments")
      .update({ preferred_at: data.preferredAt, status: "scheduled" })
      .eq("id", data.appointmentId)
      .select("lead_id")
      .single();
    if (error) throw error;

    if (appt.lead_id) {
      await context.supabase.from("leads").update({ status: "booked" }).eq("id", appt.lead_id);
      await context.supabase.from("lead_activities").insert({
        lead_id: appt.lead_id,
        business_id: data.businessId,
        kind: "appointment_confirmed",
        note: `Appointment confirmed for ${new Date(data.preferredAt).toLocaleString()}`,
      });
      await context.supabase.from("conversations").insert({
        lead_id: appt.lead_id,
        business_id: data.businessId,
        sender: "ai",
        message: `You're all set! Confirmed for ${new Date(data.preferredAt).toLocaleString()}. See you then.`,
      });
    }

    return { ok: true as const };
  });

const StatusInput = z.object({
  appointmentId: z.string().uuid(),
  businessId: z.string().uuid(),
  status: z.enum(["requested", "scheduled", "completed", "cancelled"]),
});

export const updateAppointmentStatus = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => StatusInput.parse(input))
  .handler(async ({ context, data }) => {
    const { data: appt, error } = await context.supabase
      .from("appointments")
      .update({ status: data.status })
      .eq("id", data.appointmentId)
      .select("lead_id")
      .single();
    if (error) throw error;

    if (appt.lead_id && data.status === "completed") {
      await context.supabase.from("leads").update({ status: "closed" }).eq("id", appt.lead_id);
    }
    if (appt.lead_id && data.status === "cancelled") {
      await context.supabase.from("lead_activities").insert({
        lead_id: appt.lead_id,
        business_id: data.businessId,
        kind: "appointment_cancelled",
        note: "Appointment cancelled",
      });
    }

    return { ok: true as const };
  });
    
