# BioAudit — User Guide

BioAudit checks whether an Android app's biometric login is actually **secure**, not just
whether it works. You point it at an app you own or are allowed to test, and it reports
what an attacker could do, in plain language, with a fix for each problem.

This guide is for using the app. To change the code, see
[FRONTEND-GUIDE.md](FRONTEND-GUIDE.md) and [BACKEND-GUIDE.md](BACKEND-GUIDE.md).

---

## 1. What you need

| To do this | You need |
|---|---|
| Scan an APK file | Just BioAudit. No phone. |
| Test an app on a real phone | A physical Android phone with USB debugging on. BioAudit can fetch `adb` for you |
| Anything at all | An account (free), and an internet connection |

**Why an account is required:** BioAudit does not keep a copy of your scans on your
computer. Every result is saved to your account on the server. That is what lets your
history follow you to a different computer, and what lets a team admin see that a scan
happened. With nobody signed in, there is nowhere for a result to go, so the app asks you
to sign in before it will scan anything.

There is nothing to install or configure for the server — the app already knows where to
find it.

### Getting `adb` (only for phone testing)

`adb` is Google's own tool for talking to an Android phone over USB. The easy way: open
**Assess a device** and press **Download ADB**. BioAudit fetches Google's platform-tools
(about 8 MB) into `C:\Users\<you>\BioAudit\platform-tools` and uses them from then on, with
nothing to install or add to PATH. The button only appears when the computer has no `adb`
already. The first time, the phone asks "Allow USB debugging?": unlock it and tap **Allow**,
then press **Refresh**.

To set it up yourself instead, download
[Android platform-tools](https://developer.android.com/tools/releases/platform-tools),
unzip it, and add that folder to your PATH. Check it worked:

```
adb devices
```

Your phone should appear in the list. If it says "unauthorized", unlock the phone and
accept the "Allow USB debugging?" prompt.

---

## 2. Starting the app

Double-click **BioAudit.exe**. If it is already open, opening it again brings the open
window to the front instead of starting a second copy.

The first thing you see is a welcome screen with three tabs:

| Tab | Use it when |
|---|---|
| **Create account** | You are new. As a student, pick your university, enter your university email and a password (twice, to catch typos), then press **Send code** and type in the six-digit code we email you. Tick **I accept the Terms of Use**, and your account is created once the code checks out. If the address turns out not to exist, the form tells you within a minute or two, so fix it and press **Send again**. Setting up a team instead? Choose **I'm setting up a team**. |
| **Join with an invite** | An admin sent you an invitation token for their team. Any email address works here. |
| **Sign in** | You already have an account. Forgotten your password? Click **Forgot password?** under the password box, press **Send code**, and enter the six-digit code we email you along with a new password. Resetting signs you out on every other device. |

Tick **"Keep me signed in on this computer"** to skip this next time. It starts off the
first time on purpose: staying signed in writes a long-lived token to a file in your home
folder, which is worth knowing about rather than having happen quietly. Once someone has
saved a login on this computer, it starts ticked. Leave it off on a shared or public
computer.

Closing this screen without signing in closes the app, because there is nothing it can
do signed out.

### Setting up a team

On **Create account**, choose **I'm setting up a team** and give your organisation a name.
That makes you the team's admin and unlocks the Team tab, where you can invite colleagues.

A team pays one yearly subscription that covers you and everyone you invite. Straight
after you create the team, the app offers to open the payment page; until it is paid, the
team stays locked (you cannot invite anyone yet). You can also pay later from **Team →
Subscription**. See [section 7](#7-teams) for renewing and cancelling.

Testing only your own apps as a student? Create a student account instead. You can join a
team later with **Account → Join an organisation…**

---

## 3. Scanning an APK file (no phone needed)

Use this when you have the `.apk` file itself. It reads the app's settings and compiled
code without running it.

1. Go to the **Scan an APK** tab.
2. **Browse…** to your `.apk` file.
3. Click **Run static scan**.

While it runs you will see the current step ("Reading the app's settings", "Scanning the
app's code") and a **Cancel** button. Cancel stops the run after the step it is on
finishes — a step already reading the file cannot be interrupted part way, so give it a
moment — and nothing is saved. Once the run reaches "Saving to your account", Cancel is
greyed out, because that step cannot be undone. Closing the window during a run asks
first, then stops it the same way.

When it finishes you get a list of findings, colour-coded badges showing how many of each
severity, and a PDF report written to `C:\Users\<you>\BioAudit\reports`.

### What it looks for

- The app is **debuggable** — anyone can attach a debugger and read its memory
- **`allowBackup` is on** — the app's private data can be copied off the phone with no root
- **Biometric result trusted without a bound key** — the app checks "did the fingerprint
  succeed?" and believes the answer, instead of requiring a key that only a real
  fingerprint can unlock. This is the big one, and it means the check can be bypassed.
- **Exported components with no permission guard** — screens or data providers other apps
  can reach directly

---

## 4. Testing an app on a real phone

This is the fuller test. It probes the running app on a real device, so it finds things
that reading the code cannot show.

1. Connect the phone by USB, with USB debugging on and the computer authorised.
2. Go to the **Assess a device** tab. On a computer without `adb`, press **Download ADB**
   first (see [Getting adb](#getting-adb-only-for-phone-testing)).
3. Click **Refresh** — your phone's serial should appear.
4. Click **Load apps** and pick the app, or type its package name
   (e.g. `com.example.myapp`).
5. Optionally point at the `.apk` too. **Recommended** — with it, the tool knows the full
   list of the app's entry points, which makes the headline check much more thorough.
6. **Tick the authorisation box.** The app will not proceed without it.
7. Click **Run full assessment**.

### The authorisation box is not a formality

Probing an app you do not own or have written permission to test can be a criminal
offence in many countries. The tick box is there so that confirmation is explicit and
recorded, and the server refuses to store a device assessment that does not carry it.
Only test apps you own or are explicitly authorised to test.

### What it adds over the APK scan

- **Opening the app's private screens directly** — the headline check. If a screen behind
  the login can be opened straight from outside, the biometric prompt is decorative.
- **Whether the app gives away answers to guesses** — the side-channel check. If the app
  answers a valid username differently from an invalid one, an attacker can work through
  a list and find real accounts, or brute-force a token, without ever logging in.
- **Reading the phone's log** for secrets the app leaked while running
- **Checking the backup setting** against the installed app rather than just the file

---

## 5. Reading your results

Findings are sorted most serious first and labelled:

| Severity | Meaning |
|---|---|
| **Critical** | Login can be fully bypassed |
| **High** | A secret or key is exposed, or the biometric check is decorative |
| **Medium** | A weakness that needs certain conditions to exploit |
| **Low** | Hardening gap — worth fixing, not urgent |
| **Info** | An observation, not a problem |

Findings from reading code are marked **"likely"** and dialled back one level, because
compiled code cannot be read with total certainty. Findings from probing the live app are
**"confirmed"** — the tool watched it happen.

### Getting a plain-language explanation

Turn on **Get AI explanations**, beside the Run button, before you start. Once the run is
saved, the server writes an explanation and a specific fix for each finding, aimed at a
developer with no security background, naming the actual Android setting or API to
change, and the report shows them. Explanations already written are reused, so running it
again on the same scan costs nothing. A student account gets 20 a month; team accounts are
unlimited.

### Reports

Every run writes a report automatically — a PDF if it can, HTML otherwise. **Open full
report** opens it. On a team account, **Save report from account** downloads the server's
copy, which is handy for sharing.

Where it lands depends on how you started BioAudit:

| Started from | Reports go to |
|---|---|
| The window (double-clicked exe) | `C:\Users\<you>\BioAudit\reports` |
| The command line | a `reports` folder next to wherever you ran the command |

The window uses your home folder because a double-clicked exe can start in a directory it
is not allowed to write to.

---

## 6. History

The **History** tab lists every run saved to your account, newest first — including runs
from other computers, since the account is the only place they live.

- Pick a run from the dropdown to see its full findings again
- **Reload** re-fetches from the server
- **Delete this run** / **Clear all history** remove them permanently from your account
- **Compare two runs…** (team accounts, once you have two saved runs) shows what got fixed,
  what is new, and what is unchanged between two runs of the same app. Findings are matched
  by category and component, so a genuine fix shows as resolved even though each run
  generates fresh IDs.

There are two kinds of account, and no paid plan:

| | Student account | Team account (member or admin) |
|---|---|---|
| Who | A personal account at a verified university email | Anyone who joined a team, or created one |
| History kept | Your 10 most recent runs | Everything |
| Compare runs, save report from account | No | Yes |
| AI explanations | 20 a month | Unlimited |

A student account drops its oldest run as you add more; the app tells you when that
happens. If you are removed from a team, your account becomes a student account again.

Admin accounts oversee a team rather than running scans, so they do not see the Scan,
Assess or History pages.

**Working alone?** Tick *I work alone* (CLI: `--organisation "Name" --solo`) when creating a team.
A one-person team lets you scan as its owner. Once a second person joins, the owner goes back
to overseeing only.

---

## 7. Teams

The **Team** tab appears for a team's admins. Members do not see it.

As an admin you can:

| Sub-tab | What you can do |
|---|---|
| **Members** | See everyone, view their runs, promote someone to admin, suspend or reinstate them, remove them, or delete their account |
| **Invitations** | Invite by email (creates a one-time token to send them), cancel pending invites |
| **Flagged runs** | Review scans you flagged as needing a look, then uphold or dismiss |
| **Activity log** | Every admin action, with who did it, who it affected, and when |
| **Subscription** | The organisation's yearly subscription: pay, cancel, keep, renew, or manage billing online |

### The organisation's subscription

One yearly subscription covers the admin and every member, so the whole team shares one
paid year and one renewal date. It renews and is charged automatically each year until an
admin cancels it.

- **Cancel subscription…** stops the renewal. Everyone keeps access until the end of the
  year already paid, and **Keep subscription** undoes it before then.
- **Manage billing online** opens Stripe's billing page, to cancel or change the card there.
- When the paid year ends after a cancellation, the organisation is suspended until it is
  paid again. Members are told to ask their admin and signed out; the admin can still sign
  in, but only the Subscription tab is open, to **Renew subscription…**
- Only admins can do any of this. Student accounts are not part of it.

### What an admin can and cannot see

**Members → View their runs** lists **that** a member ran a scan, against which app, and
**how many** problems of each severity it found. To read a run's findings, the admin opens
it on purpose with **Open a run…**; findings are read-only there.

Both reads, the list and each run opened, are written to the activity log, so looking at a
member's work always leaves a trace. An admin can only read the runs of people who are
currently on the team: once someone is removed, their runs are theirs alone again.

---

## 8. Your account

Everything is behind the **Account** button at the bottom of the sidebar, above your name.

- **Switch account…** — sign in as someone else (saved logins switch without a password)
- **Sign out**
- **Account details…** — who you are, your account type, your team
- **Account settings…** — change your display name, email address, or password, or delete
  your account
- **Verify student email…** — only shown while a student account still needs it
- **Join an organisation…** — redeem an invite token (only shown if you are not already
  in a team)

**Changing your password or email signs you out everywhere**, on purpose — if someone
else had your session, that ends it. Same for joining a team: your permissions change, so
the app signs you in again to pick them up.

---

## 9. Command line

Everything the window does, the terminal does too. Same exe.

```
BioAudit.exe login
BioAudit.exe scan-apk path\to\app.apk
BioAudit.exe assess --package com.example.app --apk path\to\app.apk --i-am-authorized
BioAudit.exe explain <scan-id>          # scan-apk and assess print the id
BioAudit.exe whoami
BioAudit.exe logout
```

`login` remembers you, so later commands just work. `--i-am-authorized` is the same
authorisation gate as the tick box, and `assess` refuses to run without it.

### Using it in a build pipeline

`scan-apk` and `assess` take `--format json|sarif`, `--output <file>` and
`--fail-on low|medium|high|critical`. The command exits `3` when any finding is at or
above that severity (`1` means the upload failed, `2` means not signed in), so a CI job
can fail the build. Without `--output` the report is printed to stdout after the
summary, so use `--output` when a tool reads the file.

```
bioaudit scan-apk app.apk --format sarif --output bioaudit.sarif --fail-on high
```

A pipeline has no saved session. Set `BIOAUDIT_REFRESH_TOKEN` to the refresh token of a
dedicated account (a verified student or team member, or a solo team owner) and the
command signs in from it. The token is never written to disk. Keep it in the CI secret
store. Changing that account's password or team revokes it.

There is also `BioAudit.exe dashboard`, a browser view of your account's history (needs
`streamlit` installed and a `login` first).

---

## 10. When something goes wrong

**"Not signed in"** — run `login`, or sign in through the welcome screen. Nothing scans
without an account.

**"Not saved to your account", with a "Retry saving" button** — the findings are on
screen and the report is written, but the run is not stored anywhere yet, because there
is no local copy to fall back on. Check your connection and click **Retry saving** before
closing the run. On the command line, just run the same command again.

**"Too many attempts. Try again in a few minutes."** — the server limits sign-in attempts
to 20 per 15 minutes per computer, to stop password guessing. Wait it out.

**"Too many failed attempts. Try again in N minutes."** — five wrong passwords for one
email address lock it for 15 minutes (longer if it happens again, up to an hour). Even the
right password is refused until then. **Forgot password?** lifts the lock straight away.

**"ADB is not set up on this computer"** — press **Download ADB** beside the device list.

**"Phone found, not allowed yet"** — the phone has not approved this computer. Unlock it,
tap **Allow** on the "Allow USB debugging?" prompt, then press **Refresh**. No prompt? Unplug
and replug the phone, or in Developer options use **Revoke USB debugging authorisations**,
then plug in again.

**"Phone found but offline"** — unplug the phone, plug it back in, and press **Refresh**.

**No device in the Assess tab** — check `adb devices` in a terminal. If it is empty:
confirm USB debugging is on, try a different cable or port, and unlock the phone to accept
the authorisation prompt. If it says "unauthorized", that prompt is what is missing. Some
phone brands also need their maker's USB driver on Windows.

**"Package ... is not installed"** — the package name is wrong. Use **Load apps** to pick
from the real list instead of typing it.

**A scan seems stuck** — look at the step text under the buttons. Reading a large APK
genuinely takes a while. **Cancel** stops it at the next step boundary.

**Explanations do nothing** — the AI layer needs a key configured on the server, not on
your computer. Check `/api/health` on the server; it reports `aiExplanations` as enabled
or disabled. Findings, severities, and fallback fixes all work regardless.

**"The AI service has hit its request limit"** — the AI provider's free limit was reached.
Try again in a minute; findings that already have an explanation are skipped.

**"Your organisation's subscription has ended" or "has not been paid yet"** — ask your
team's admin to pay or renew it from **Team → Subscription**.

**Nothing happens when I open BioAudit** — it is probably already open. Look for the window
or its taskbar icon.

**The app crashed** — there is a log at `bioaudit-crash.log` in your home folder.

---

## 11. Ethics

BioAudit does no more to an app than is needed to *demonstrate* a finding, and every
runtime test is gated behind explicit authorisation. Test only what you own or are
explicitly permitted to test. Finding a real vulnerability in someone else's app means
reporting it to them, not using it.
