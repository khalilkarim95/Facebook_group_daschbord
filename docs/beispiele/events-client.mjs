/**
 * Reference client for POST https://go.b-tarikak.de/events
 *
 * Hand this to whoever maintains btarikak-api. It is written in English on
 * purpose: it is the companion of docs/events-api.html, addressed to the same
 * reader. The fbgroups project itself is documented in German.
 *
 * Node 18+, no dependencies (global fetch). Nothing here is fbgroups-specific
 * except the endpoint and the header name.
 *
 * The three rules that matter, all from the contract:
 *
 *  1. Never block a user-facing request on this call. A signup must not fail
 *     because a tracking service is slow. Write an outbox row inside the same
 *     transaction as the business change, and drain it from a worker.
 *  2. Only clicks are deduplicated on the receiving side. A blindly retried
 *     registration is counted twice and inflates the funnel. Retry solely on
 *     5xx and network errors - never on 401 (configuration) or 422 (payload).
 *  3. Send `tracking_code` on the first event where you know it. Later events
 *     may omit it: the service reuses the user's first attribution, which is
 *     the group that actually brought the person.
 */

const ENDPOINT = process.env.FBGROUPS_EVENTS_URL ?? "https://go.b-tarikak.de/events";
const TOKEN = process.env.EVENTS_TOKEN ?? "";

/** The six stages. `click` is recorded by the redirect service - never send it. */
export const Stage = {
  LANDING_VISIT: "landing_visit",
  REGISTRATION: "registration",
  ACTIVATION: "activation",
  QUALIFIED: "qualified",
  CONVERSION: "conversion",
};

/** Thrown for 401/422: the call was rejected and must not be retried. */
export class PermanentEventError extends Error {
  constructor(status, body) {
    super(`events rejected with ${status}: ${body}`);
    this.status = status;
    this.permanent = true;
  }
}

/**
 * Sends one event. Resolves with the parsed response body.
 *
 * @param {object} event
 * @param {string} event.eventType   one of Stage.*
 * @param {string} event.userRef     your opaque user id - never a name or e-mail
 * @param {string} [event.trackingCode]  the `ref` value from the landing URL
 * @param {string} [event.referralCode]  only on registration, if invited
 * @param {Date}   [event.occurredAt]    when it happened on your side
 * @param {number} [event.timeoutMs]
 */
export async function sendEvent({
  eventType,
  userRef,
  trackingCode = "",
  referralCode = "",
  occurredAt = null,
  timeoutMs = 4000,
}) {
  if (!TOKEN) throw new Error("EVENTS_TOKEN is not configured");

  const payload = { event_type: eventType, user_ref: userRef };
  if (trackingCode) payload.tracking_code = trackingCode;
  if (referralCode) payload.referral_code = referralCode;
  // Send it whenever the event is not "right now" - draining a backlog hours
  // later without it would date every event to the moment of the drain.
  if (occurredAt) payload.occurred_at = occurredAt.toISOString();

  const abbruch = AbortSignal.timeout(timeoutMs);
  const antwort = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Events-Token": TOKEN },
    body: JSON.stringify(payload),
    signal: abbruch,
  });

  const text = await antwort.text();
  if (antwort.status === 401 || antwort.status === 422) {
    throw new PermanentEventError(antwort.status, text);
  }
  if (!antwort.ok) {
    // 5xx: unknown whether it was stored. The caller retries with backoff.
    throw new Error(`events failed with ${antwort.status}: ${text}`);
  }
  return text ? JSON.parse(text) : {};
}

/**
 * Drains one outbox row. Returns true when the row may be marked as sent.
 *
 * Mark the row `sent` on success AND on a permanent error: a 401 or 422 will
 * not become valid by waiting, and an un-drainable row blocks the queue behind
 * it. Log it loudly instead.
 */
export async function deliver(row, { attempts = 2, backoffMs = 2000 } = {}) {
  for (let versuch = 1; versuch <= attempts; versuch += 1) {
    try {
      const ergebnis = await sendEvent(row);
      return { sent: true, result: ergebnis };
    } catch (fehler) {
      if (fehler.permanent) return { sent: true, rejected: fehler.message };
      if (versuch === attempts) return { sent: false, error: fehler.message };
      await new Promise((r) => setTimeout(r, backoffMs * versuch));
    }
  }
  return { sent: false };
}

/* ---------------------------------------------------------------------------
 * Where each call belongs in btarikak-api
 * -------------------------------------------------------------------------*/

/**
 * The visitor arrived from a Facebook group: b-tarikak.de was loaded with
 * ?ref=FB-... This is the cheapest stage to add and the only one that
 * separates "the landing page is broken" from "the audience is wrong".
 */
export const onLandingVisit = (userRef, ref) =>
  sendEvent({ eventType: Stage.LANDING_VISIT, userRef, trackingCode: ref });

/**
 * Account created. Carries the most weight: the response contains this user's
 * own referral code (display it), and `referral` reports what happened to the
 * invitation that was used. A rejected referral does not invalidate the
 * registration - the person did sign up.
 */
export const onRegistration = (userRef, ref, invitedWith = "") =>
  sendEvent({
    eventType: Stage.REGISTRATION,
    userRef,
    trackingCode: ref,
    referralCode: invitedWith,
  });

/**
 * Stages 04-06 are named generically because the tracking service is not
 * specific to this product. What they mean for b-tarikak is a business
 * decision that must be made once and kept: changing it later makes old and
 * new numbers incomparable without any visible sign that they are.
 *
 * Proposals from the contract, to confirm or replace:
 *   activation  - identity verification passes (btarikak-verify)
 *   qualified   - first trip offered or first request posted
 *   conversion  - a match is confirmed by both sides and the item handed over
 */
export const onActivation = (userRef) =>
  sendEvent({ eventType: Stage.ACTIVATION, userRef });
export const onQualified = (userRef) =>
  sendEvent({ eventType: Stage.QUALIFIED, userRef });
export const onConversion = (userRef) =>
  sendEvent({ eventType: Stage.CONVERSION, userRef });

/* ---------------------------------------------------------------------------
 * Connectivity check - run once from inside the API container:
 *
 *   EVENTS_TOKEN=... node events-client.mjs
 * -------------------------------------------------------------------------*/
if (import.meta.url === `file://${process.argv[1]}`) {
  const ergebnis = await sendEvent({
    eventType: Stage.LANDING_VISIT,
    userRef: "connectivity-check",
  });
  console.log("ok:", ergebnis);
}
