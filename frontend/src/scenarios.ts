// The four RevGuard recovery workflows, as demoable scenarios. Each one posts synthetic events
// to POST /events; the real backend detector decides whether to raise a case (the UI never
// fabricates cases), and POST /cases/{id}/run drives the real detect→diagnose→policy→execute→
// verify pipeline. The metadata here is purely descriptive copy for the cards.

import type { EventIn, WorkflowType } from "./types";

export interface Scenario {
  key: string;
  workflow: WorkflowType;
  title: string;
  detected: string; // what revenue risk was detected
  amountLabel: string; // amount at risk (display)
  intervention: string; // what the agent may consider
  why: string; // why this workflow exists
  expect: string; // short hint of the likely policy outcome
  build: () => EventIn[];
}

function rand(): string {
  return Math.random().toString(36).slice(2, 8);
}

export const SCENARIOS: Scenario[] = [
  {
    key: "subscription",
    workflow: "failed_subscription",
    title: "Failed subscription",
    detected: "A recurring charge was declined (card_declined).",
    amountLabel: "₹1,500",
    intervention: "Retry the payment on a bounded schedule.",
    why: "Recoverable involuntary churn — a retry often collects the charge.",
    expect: "Usually APPROVE → retry → verify",
    build: () => [
      {
        event_type: "subscription_payment_failed",
        source: "synthetic",
        customer_id: "cust_demo_sub",
        subscription_id: `sub_${rand()}`,
        amount: "1500.00",
        currency: "INR",
        metadata: { failure_reason: "card_declined" },
      },
    ],
  },
  {
    key: "checkout",
    workflow: "checkout_abandonment",
    title: "Checkout abandonment",
    detected: "A customer left checkout with items still in the cart.",
    amountLabel: "₹6,000",
    intervention: "Create a real Razorpay Test Mode Payment Link for the cart amount.",
    why: "Near-conversion revenue a timely payment link can recover.",
    expect: "Payment link → verify",
    build: () => [
      {
        event_type: "checkout_abandoned",
        source: "synthetic",
        customer_id: "cust_demo_checkout",
        order_id: `order_${rand()}`,
        amount: "6000.00",
        currency: "INR",
        metadata: { abandonment_stage: "payment" },
      },
    ],
  },
  {
    key: "overdue",
    workflow: "overdue_receivable",
    title: "Overdue receivable",
    detected: "A large invoice is 65 days overdue.",
    amountLabel: "₹90,000",
    intervention: "High value → the PolicyEngine escalates for human review.",
    why: "Demonstrates the safety gate: the agent will not auto-act on large sums.",
    expect: "Expect ESCALATE (no auto-action)",
    build: () => [
      {
        event_type: "invoice_overdue",
        source: "synthetic",
        customer_id: "cust_demo_invoice",
        invoice_id: `inv_${rand()}`,
        amount: "90000.00",
        currency: "INR",
        metadata: { days_overdue: 65 },
      },
    ],
  },
  {
    key: "degradation",
    workflow: "payment_degradation",
    title: "Payment degradation",
    detected: "A burst of failures on one method (16 failed / 20 attempts).",
    amountLabel: "₹12,800",
    intervention: "Root-cause the failures by method/issuer/error code, then escalate mitigation.",
    why: "A method-wide failure spike putting many payments at risk.",
    expect: "Root cause → escalate",
    build: () => {
      // Unique per run so each demo is a clean window (the detector groups degradation by
      // method across all stored events; a fresh method keeps the root cause concentrated).
      const method = `card_netbank_${rand()}`;
      const events: EventIn[] = [];
      // A concentrated root cause: most failures share one error code + issuer, so the
      // detector can name the dominant segment rather than only "failures went up".
      for (let i = 0; i < 16; i++) {
        const dominant = i < 10; // 10/16 ≈ 62% share on the dominant segment
        events.push({
          event_type: "payment_failed",
          source: "synthetic",
          payment_id: `pay_f_${rand()}_${i}`,
          amount: "800.00",
          currency: "INR",
          metadata: {
            method,
            issuer: dominant ? "HDFC" : i % 2 ? "ICICI" : "AXIS",
            error_code: dominant ? "gateway_timeout" : "insufficient_funds",
          },
        });
      }
      for (let i = 0; i < 4; i++)
        events.push({
          event_type: "payment_succeeded",
          source: "synthetic",
          payment_id: `pay_s_${rand()}_${i}`,
          amount: "800.00",
          currency: "INR",
          metadata: { method, issuer: "HDFC" },
        });
      return events;
    },
  },
];
