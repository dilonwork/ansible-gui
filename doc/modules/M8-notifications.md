# M8 — Notifications

> Goal: push what people need to know in front of them; rules configurable, quiet hours respected, mobile (LINE) is the first-priority channel.

## 8.1 Job notifications (P1)

**Description**: push on job outcome — no need to stare at logs.

**Scenario**: the Sunday 02:00 scheduled patch finishes; Dylan wakes up to a LINE message: "9/9 succeeded, took 18 minutes" — no need to open a computer.

**Details**
- Events: job success / job failure (with failed-host summary) / job rejected by approver / schedule missed (see 8.2)
- Content: job name, target scope, x/y hosts succeeded, duration, detail link (one tap into job history)
- Per-template notification policy: notify on failure only / notify always / never (noisy routine jobs can be muted)

**Acceptance criteria**: a job with 1 of 3 hosts failed notifies "2/3 succeeded" and lists the failed hostname.

## 8.2 Node anomaly notifications (P1)

**Description**: proactive alerts when machines and clusters misbehave.

**Details**
- Events: node NotReady / NotReady recovered, SSH reachability flips (M1.3), version drift found (M5.4), credential expiring (M7), schedule missed
- Dedup and convergence: repeat NotReady from the same node within 1 hour pushes only once, reset on recovery; no alert storms
- Severity tiers: red (NotReady, job failure) / amber (drift, expiring credential); red events ignore quiet hours (see rule engine below)

**Acceptance criteria**: unplug a test machine's network — NotReady notification arrives within 5 minutes; no repeat push within 1 hour; recovery notification arrives after it's back.

## 8.3 LINE push (P1)

**Description**: the most-used mobile channel for Taiwan teams; the main battleground for the approval scenario (M3.6).

**Scenario**: Dylan gets a LINE message while out: "kubelet-upgrade / target: 3 workers / requester: Dylan"; tapping the link shows the parameter summary, pressing "Approve" starts the job.

**Details**
- Via the LINE Messaging API (1:1 push + buttons); LINE Notify supported as the simple flavor initially
- Approval request messages include: template name, targets, parameter summary, requester, plus "Approve / Reject" buttons (tapping opens the approval page; identity is verified in the mobile browser before taking effect — never executed directly inside the chat, for safety)
- Binding: users bind LINE in personal settings (QR-code pairing), one binding per person

**Acceptance criteria**: < 30 seconds from approval request creation to LINE delivery; approvers without LINE bound fall back to Email — no request is lost.

## 8.4 Slack / Teams (P2)

**Description**: supplementary channels for cross-border/cross-team collaboration.

**Details**
- Incoming Webhook integration; message templates share the same event rendering as LINE (same engine, different skin)
- Channel-level subscriptions: different events can go to different channels (e.g. #alerts only gets red events)

## Rule engine & quiet hours (P1, shared with 8.1/8.2)

**Description**: the master switch matrix for notifications — avoiding "push everything = read nothing".

**Details**
- Rule = event type × scope (cluster/group/global) × channel (Webhook/Email/LINE); multiple rules stack
- Quiet hours: configurable (e.g. daily 00:00–07:00); during quiet hours amber events are only recorded, never pushed; red events still push (configurable)
- Channels:
- Webhook: generic POST with a fixed payload schema (event, severity, time, detail link); can feed PagerDuty/home-grown systems
- Email: SMTP settings (host/port/TLS/credentials), HTML + plain-text dual format
- Notification delivery is queryable (sent or not, where to, success or not); failed sends retry 3 times, then marked failed and flagged in the UI

**Acceptance criteria**: with quiet hours set, amber events in-window appear only in the notification log with no phone push; red events still push.

---

**Non-goals of this module**: SMS/voice-call alerts, on-call scheduling and escalation policy, closed-loop auto-remediation of alert events.
