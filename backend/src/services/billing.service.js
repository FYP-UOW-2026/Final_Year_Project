/**
 * The yearly organisation subscription.
 *
 * An organisation pays once a year for everyone in it, so its owner's Stripe subscription
 * is the organisation's subscription: every member shares the same paid year and the
 * same renewal date. Stripe and the "Run Payments with Stripe" extension do the billing.
 * The extension mirrors each subscription into customers/{uid}/subscriptions/{id}, and
 * this service reads that mirror to decide whether an organisation may use the product.
 *
 * Cancelling follows Stripe's own model: it sets cancel_at_period_end, so the year already
 * paid for runs to its end and nothing more is charged. Stripe moves the subscription to
 * "canceled" when the year ends, and that is the moment the organisation is suspended.
 * Without a cancellation Stripe renews and charges automatically each year.
 */
import { db, FieldValue } from "../config/firebase.js";
import { env } from "../config/env.js";
import { COLLECTIONS } from "../constants/index.js";
import { ApiError } from "../utils/ApiError.js";

/** Stripe statuses during which the organisation keeps full access. */
const LIVE_STATUSES = ["active", "trialing", "past_due"];

export const BILLING_STATE = {
  NONE: "none", // never subscribed (an in-app or pre-billing organisation)
  ACTIVE: "active", // paid and set to renew
  CANCELLING: "cancelling", // paid until the end of the year, will not renew
  ENDED: "ended", // the paid year is over: everyone is suspended until payment
};

/** Refusal code the desktop app recognises, so it can explain rather than just fail. */
export const SUSPENDED_CODE = "ORGANISATION_SUBSCRIPTION_ENDED";

// A short cache, because the check runs on every request from an organisation's members.
// Cancelling or renewing clears it, so the person who acted sees the change at once.
// Off against the emulators, where tests change a subscription and check at once.
const CACHE_MS = env.usingEmulators ? 0 : 30_000;
const cache = new Map();

export function clearCache(orgId) {
  cache.delete(orgId);
}

const customers = () => db.collection("customers");

function toDate(value) {
  if (!value) return null;
  if (typeof value.toDate === "function") return value.toDate();
  if (typeof value === "number") return new Date(value * 1000);
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** The subscription that speaks for the organisation: a live one if any, else the newest. */
async function findSubscription(ownerId) {
  const snap = await customers().doc(ownerId).collection("subscriptions").get();
  if (snap.empty) return null;
  const subs = snap.docs
    .map((d) => ({ id: d.id, ...d.data() }))
    .sort((a, b) => (toDate(b.created)?.getTime() ?? 0) - (toDate(a.created)?.getTime() ?? 0));
  return subs.find((s) => LIVE_STATUSES.includes(s.status)) ?? subs[0];
}

function summarise(org, sub) {
  if (!sub) {
    const required = env.stripe.requireForOrganisations;
    return {
      state: required ? BILLING_STATE.ENDED : BILLING_STATE.NONE,
      active: !required,
      subscriptionId: null,
      status: null,
      periodEnd: null,
      ownerId: org.ownerId,
    };
  }
  const live = LIVE_STATUSES.includes(sub.status);
  let state = BILLING_STATE.ENDED;
  if (live) state = sub.cancel_at_period_end ? BILLING_STATE.CANCELLING : BILLING_STATE.ACTIVE;
  return {
    state,
    active: live,
    subscriptionId: sub.id,
    status: sub.status,
    // Shared by everyone in the organisation: when the paid year renews or ends.
    periodEnd: toDate(sub.current_period_end)?.toISOString() ?? null,
    ownerId: org.ownerId,
  };
}

/** The organisation's subscription state. Cached briefly; see CACHE_MS. */
export async function statusFor(org) {
  const hit = cache.get(org.id);
  if (hit && hit.expires > Date.now()) return hit.value;
  const value = summarise(org, await findSubscription(org.ownerId));
  cache.set(org.id, { value, expires: Date.now() + CACHE_MS });
  return value;
}

/** The status, by organisation id. Returns null for an organisation that no longer exists. */
export async function statusForId(orgId) {
  const snap = await db.collection(COLLECTIONS.ORGANISATIONS).doc(orgId).get();
  if (!snap.exists) return null;
  return statusFor({ id: snap.id, ...snap.data() });
}

/** The refusal sent to anyone in a suspended organisation. */
export function suspendedError(isAdmin) {
  return new ApiError(
    403,
    isAdmin
      ? "Your organisation's subscription has ended, so its accounts are suspended. " +
          "Renew the subscription to restore access for everyone."
      : "Your organisation's subscription has ended, so its accounts are suspended. " +
          "Ask your organisation's admin to renew it.",
    undefined,
    SUSPENDED_CODE
  );
}

// --- Acting on the subscription through Stripe --------------------------------

async function stripe(path, params) {
  if (!env.stripe.enabled) {
    throw ApiError.unavailable(
      "Billing is not set up on this server yet. Contact the BioAudit administrator."
    );
  }
  const response = await fetch(`${env.stripe.apiBase}/v1/${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.stripe.secretKey}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams(params).toString(),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // Stripe's own message can name internal ids, so it goes to the log, not the user.
    console.error(`[stripe] ${path} failed`, response.status, body?.error?.message);
    throw ApiError.unavailable("The payment provider could not complete that. Try again shortly.");
  }
  return body;
}

/**
 * Stop, or undo stopping, the automatic renewal at the end of the paid year.
 *
 * The extension mirrors the change into Firestore when Stripe's webhook arrives; the
 * mirror is also updated here so the app shows the new state straight away.
 */
export async function setCancelAtPeriodEnd(org, cancel) {
  const current = await statusFor(org);
  if (!current.subscriptionId || current.state === BILLING_STATE.ENDED) {
    throw ApiError.conflict("Your organisation has no subscription that is still running.");
  }
  if (cancel && current.state === BILLING_STATE.CANCELLING) {
    throw ApiError.conflict(`The subscription is already set to end on ${current.periodEnd}.`);
  }
  if (!cancel && current.state === BILLING_STATE.ACTIVE) {
    throw ApiError.conflict("The subscription is already set to renew.");
  }

  await stripe(`subscriptions/${current.subscriptionId}`, {
    cancel_at_period_end: cancel ? "true" : "false",
  });
  await customers()
    .doc(org.ownerId)
    .collection("subscriptions")
    .doc(current.subscriptionId)
    .set({ cancel_at_period_end: cancel, updatedByApi: FieldValue.serverTimestamp() }, { merge: true });

  clearCache(org.id);
  return statusFor(org);
}

async function stripeCustomerId(org) {
  const snap = await customers().doc(org.ownerId).get();
  const id = snap.exists ? snap.data().stripeId : null;
  if (!id) {
    throw ApiError.conflict(
      "This organisation has not paid through Stripe yet, so there is no billing account to open."
    );
  }
  return id;
}

/** A link to Stripe's billing page, where the admin can cancel or change the card. */
export async function billingPortalUrl(org) {
  const session = await stripe("billing_portal/sessions", {
    customer: await stripeCustomerId(org),
    return_url: env.stripe.returnUrl,
  });
  return session.url;
}

/** A Stripe checkout link that starts a new yearly subscription for a lapsed organisation. */
export async function renewalCheckoutUrl(org) {
  if (!env.stripe.priceId) {
    throw ApiError.unavailable(
      "Renewing from the app is not set up yet. Renew on the BioAudit website instead."
    );
  }
  const current = await statusFor(org);
  if (current.active && current.state !== BILLING_STATE.NONE) {
    throw ApiError.conflict("The subscription is still running, so there is nothing to renew.");
  }
  const session = await stripe("checkout/sessions", {
    mode: "subscription",
    customer: await stripeCustomerId(org),
    "line_items[0][price]": env.stripe.priceId,
    "line_items[0][quantity]": "1",
    "subscription_data[metadata][organisationId]": org.id,
    success_url: env.stripe.returnUrl,
    cancel_url: env.stripe.returnUrl,
  });
  clearCache(org.id);
  return session.url;
}
