"""Command-line entry point and orchestration.

Commands:
  login   [--email E]                     sign in and remember the session
  logout                                  end the saved session
  register --email E --password P         create an account (add --organisation to admin
                                           a team, or --join-token to join one from an invite)
  whoami                                  show who is currently signed in
  verify-student [--code C] [--resend]    confirm a university email for a student account
  scan-apk <apk> [--offline]              static analysis only (no device); --offline needs
                                           no account and uploads nothing (for CI)
  init-ci [--apk PATH]                    write a GitHub Actions workflow that scans the APK
  assess  --package P --i-am-authorized   full black-box assessment on a device
  explain <scan-id>                       ask the server to explain a saved run's findings
  dashboard                               launch the Streamlit UI
  gui                                     launch the PySide6 desktop app

`scan-apk` and `assess` need a signed-in session (`bioaudit login` first): BioAudit keeps
no local copy of a scan, so the account you upload to is the only place a run is stored.

AI explanation is server-side only, by `explain` (mirrors the GUI's "Explain findings"
button) -- there used to also be a `--explain` flag on `scan-apk` that ran a local AI
call using a `GEMINI_API_KEY` read off the calling machine's own environment. It was
removed because that meant a flag that only worked on whichever computer happened to
have that variable set, and silently did nothing everywhere else.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import core
from .adb import AdbError
from .api import ApiClient, ApiClientError
from .config import Config
from .models import TestRun
from .report import export
from .session import default_base_dir, list_accounts, restore_session, save_session, switch_to

# 1 = upload failed, 2 = not signed in / not authorised, 3 = findings hit --fail-on,
# 4 = the input could not be scanned (missing, corrupt, not an APK).
EXIT_THRESHOLD = 3
EXIT_INPUT = 4

_NOT_SIGNED_IN = (
    "Not signed in. BioAudit stores every scan on your account, so run "
    "`bioaudit login` first (or `bioaudit register` if you don't have one yet)."
)


def _require_session(cfg: Config) -> ApiClient | None:
    client = restore_session(default_base_dir(), cfg.api["base_url"])
    if client is None:
        print(_NOT_SIGNED_IN, file=sys.stderr)
    return client


def cmd_login(args, cfg: Config) -> int:
    server = args.server or cfg.api["base_url"]
    email = args.email or input("Email: ").strip()
    password = args.password or getpass.getpass("Password: ")

    client = ApiClient(server)
    try:
        client.login(email, password)
    except ApiClientError as exc:
        print(f"Sign-in failed: {exc}", file=sys.stderr)
        return 1

    if not args.no_remember:
        save_session(default_base_dir(), client)
    print(f"Signed in as {client.account.email} ({client.account.tier}"
          f"{', admin' if client.account.is_admin else ''}).")
    return 0


def cmd_logout(args, cfg: Config) -> int:
    base_dir = default_base_dir()
    client = restore_session(base_dir, cfg.api["base_url"])
    if client is not None:
        client.logout()
    from .session import clear_session
    clear_session(base_dir)
    print("Signed out.")
    return 0


def cmd_accounts(args, cfg: Config) -> int:
    saved = list_accounts(default_base_dir())
    if not saved:
        print("No saved accounts. Sign in with `bioaudit login` (without --no-remember).")
        return 0
    for acct in saved:
        print(f"{'*' if acct.active else ' '} {acct.email}")
    print("\n* = active. Change with `bioaudit switch <email>`.")
    return 0


def cmd_switch(args, cfg: Config) -> int:
    base_dir = default_base_dir()
    if args.email not in {a.email for a in list_accounts(base_dir)}:
        print(f"{args.email} is not a saved account. See `bioaudit accounts`.", file=sys.stderr)
        return 1
    client = switch_to(base_dir, args.email, cfg.api["base_url"])
    if client is None:
        print(f"The saved sign-in for {args.email} has expired. "
              "Run `bioaudit login` to sign in again.", file=sys.stderr)
        return 1
    print(f"Switched to {client.account.email} ({client.account.tier}"
          f"{', admin' if client.account.is_admin else ''}).")
    return 0


def cmd_register(args, cfg: Config) -> int:
    if args.organisation and args.join_token:
        print("--organisation and --join-token cannot both be given: an account either "
              "starts a new team or joins an existing one.", file=sys.stderr)
        return 2

    server = args.server or cfg.api["base_url"]
    password = args.password or getpass.getpass("Password (at least 8 characters): ")
    if len(password) < 8:
        print("Use a password of at least 8 characters.", file=sys.stderr)
        return 2

    if not args.accept_terms:
        print("BioAudit may only be used on apps you own or are authorised to test "
              "(see docs/TERMS.md).")
        answer = input("Do you accept the Terms of Use? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("You must accept the Terms of Use to create an account.", file=sys.stderr)
            return 2

    client = ApiClient(server)
    try:
        if args.organisation:
            client.register_admin(args.email, password, args.organisation, args.name,
                                  tos_accepted=True, solo=args.solo)
        else:
            client.register(args.email, password, args.name,
                            university_id=args.university, tos_accepted=True)
            if args.join_token:
                # Joining revokes the session server-side so the new team permissions
                # take effect, so a fresh sign-in follows, matching the GUI's join flow.
                client.join_organisation(args.join_token)
                client.login(args.email, password)
    except ApiClientError as exc:
        print(f"Registration failed: {exc}", file=sys.stderr)
        return 1

    if not args.no_remember:
        save_session(default_base_dir(), client)
    print(f"Registered and signed in as {client.account.email} ({client.account.tier}"
          f"{', admin' if client.account.is_admin else ''}).")
    if client.account.needs_student_verification:
        if client.account.student_verification.get("status") == "pending":
            print(f"A six-digit code has been emailed to {client.account.email}. "
                  "Enter it with `bioaudit verify-student` to unlock scanning.")
        else:
            print("A student account needs a verified university email before you can scan. "
                  "Run `bioaudit verify-student`.")
    return 0


def cmd_whoami(args, cfg: Config) -> int:
    client = restore_session(default_base_dir(), cfg.api["base_url"])
    if client is None:
        print("Not signed in.")
        return 1
    account = client.account
    print(f"{account.email}  ·  {account.tier}"
          f"{'  ·  admin' if account.is_admin else ''}"
          f"{'  ·  org ' + account.organisation_id if account.organisation_id else ''}"
          f"{'  ·  student email unverified' if account.needs_student_verification else ''}")
    return 0


def cmd_verify_student(args, cfg: Config) -> int:
    """Confirm the account's university email with the code sent to it.

    Mirrors the GUI's verification dialog. With no code waiting yet (or with --resend),
    a new one is emailed first; then the code is taken from --code or prompted for.
    """
    if args.code and args.resend:
        print("--code and --resend cannot be combined: a new code replaces the old one.",
              file=sys.stderr)
        return 2

    client = _require_session(cfg)
    if client is None:
        return 2
    account = client.account
    sv = account.student_verification

    if not account.needs_student_verification:
        print("Your student email is already verified." if sv.get("status") == "verified"
              else "This account does not need student verification.")
        return 0
    if not sv.get("eligible"):
        university = (sv.get("university") or {}).get("name")
        print(f"{account.email} is not {'a ' + university if university else 'a university'} "
              "email address, so no code can be sent to it. Personal accounts are for "
              "students: change your account email to your university address in the app, "
              "or join a team with an invitation from its admin.", file=sys.stderr)
        return 1

    try:
        if args.resend or (not args.code and sv.get("status") != "pending"):
            print(client.send_student_code())
        code = args.code or input("Six-digit code from the email: ").strip()
        client.verify_student_code(code)
    except ApiClientError as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1

    print("Your university email is verified. You can now scan.")
    return 0


def _apk_problem(path: str) -> str | None:
    """Why `path` cannot be scanned, or None when it looks like a plain APK. Checked before
    androguard sees the file so a bad input is a clear message, not a traceback."""
    import zipfile
    if not os.path.isfile(path):
        return f"APK not found: {path}"
    if path.lower().endswith(".aab"):
        return (f"{path} is an Android App Bundle (.aab). Scan an APK instead, e.g. the "
                "output of `./gradlew assembleRelease`.")
    if not zipfile.is_zipfile(path):
        return f"{path} is not a valid APK (not a zip archive)."
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    if "BundleConfig.pb" in names:
        return f"{path} is an Android App Bundle, not an APK. Scan an APK instead."
    if "AndroidManifest.xml" not in names:
        return f"{path} is not a valid APK (no AndroidManifest.xml)."
    return None


def cmd_scan_apk(args, cfg: Config) -> int:
    offline = args.offline or os.environ.get("BIOAUDIT_OFFLINE") == "1"
    client = None
    if not offline:
        client = _require_session(cfg)
        if client is None:
            return 2
    problem = _apk_problem(args.apk)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_INPUT
    try:
        run = core.build_scan_apk(args.apk, cfg)
    except Exception as exc:  # androguard raises assorted types on a corrupt archive
        print(f"Could not analyse {args.apk}: {exc}", file=sys.stderr)
        return EXIT_INPUT
    # A static scan reads a file the user chose, so there is no live app to authorise
    # probing against; the GUI treats this the same way.
    return _finish(run, cfg, client, authorised=True,
                   apk_file_name=os.path.basename(args.apk), client_name=args.client,
                   fmt=args.format, output=args.output, fail_on=args.fail_on,
                   no_report=args.no_report)


_CI_WORKFLOW = """\
name: BioAudit scan

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  bioaudit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/checkout@v4
        with:
          repository: FYP-UOW-2026/Final_Year_Project
          path: .bioaudit

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: .bioaudit/requirements.txt

      - name: Install BioAudit
        run: pip install -r .bioaudit/requirements.txt

      - name: Scan APK
        env:
          PYTHONPATH: .bioaudit
          QT_QPA_PLATFORM: offscreen
        run: >
          python -m bioaudit scan-apk "{apk}" --offline --no-report
          --format markdown --output "$GITHUB_STEP_SUMMARY" --fail-on {fail_on}
"""


def cmd_init_ci(args, cfg: Config) -> int:
    """Write .github/workflows/bioaudit.yml. The APK path is resolved relative to the repo
    root so the workflow works from a clean checkout."""
    import glob
    root = os.path.abspath(args.dir)
    apk = args.apk
    if not apk:
        found = sorted(glob.glob(os.path.join(root, "**", "build", "outputs", "apk", "**",
                                              "*.apk"), recursive=True))
        if not found:
            print("No APK found under build/outputs/apk. Pass --apk PATH "
                  "(relative to the repo root).", file=sys.stderr)
            return EXIT_INPUT
        apk = os.path.relpath(found[0], root)
    elif os.path.isabs(apk):
        apk = os.path.relpath(apk, root)
    apk = apk.replace(os.sep, "/")
    if apk.startswith(".."):
        print(f"{apk} is outside the repository root {root}.", file=sys.stderr)
        return EXIT_INPUT
    if not os.path.isfile(os.path.join(root, apk)):
        print(f"Warning: {apk} does not exist yet; the workflow will fail until it is "
              "committed or built.", file=sys.stderr)
    out = os.path.join(root, ".github", "workflows", "bioaudit.yml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(_CI_WORKFLOW.format(apk=apk, fail_on=args.fail_on))
    print(f"Wrote {os.path.relpath(out, root)} (scans {apk}, fails on {args.fail_on}+).")
    return 0


def cmd_assess(args, cfg: Config) -> int:
    if not args.i_am_authorized:
        print("Refusing to test: pass --i-am-authorized to confirm you own or are "
              "authorized to test this app.", file=sys.stderr)
        return 2

    client = _require_session(cfg)
    if client is None:
        return 2

    if not args.apk:
        print("Note: no --apk supplied; will pull the installed app off the device "
              "to read its components. Pass --apk to skip the pull or test offline.",
              file=sys.stderr)

    try:
        run = core.build_assess(args.package, args.apk, cfg)
    except AdbError as exc:
        print(f"Device error: {exc}", file=sys.stderr)
        return 1

    apk_name = os.path.basename(args.apk) if args.apk else None
    return _finish(run, cfg, client, authorised=True,
                   apk_file_name=apk_name, client_name=args.client,
                   fmt=args.format, output=args.output, fail_on=args.fail_on)


def cmd_explain(args, cfg: Config) -> int:
    """Ask the server to write a plain-language explanation and fix for a saved run's
    findings. Mirrors the GUI's "Explain findings" button -- same server endpoint, same
    "skip anything already explained" behaviour, so paying for one twice never happens
    from either front end."""
    client = _require_session(cfg)
    if client is None:
        return 2

    try:
        scan = client.get_scan(args.scan_id)
    except ApiClientError as exc:
        print(f"Could not load that scan: {exc}", file=sys.stderr)
        return 1

    findings = scan.get("findings", [])
    if not findings:
        print("That run has no findings.")
        return 0

    explained = 0
    failures = []
    for index, finding in enumerate(findings):
        if finding.get("explanation") and finding.get("mitigation"):
            continue
        try:
            client.explain_finding(args.scan_id, index)
            explained += 1
            print(f"  explained: {finding.get('title', 'finding')}")
        except ApiClientError as exc:
            failures.append(f"{finding.get('title', 'finding')}: {exc}")

    if explained == 0 and not failures:
        print("Every finding in that run already has an explanation.")
        return 0

    print(f"\nExplained {explained} finding(s).")
    for line in failures:
        print(f"  could not explain: {line}", file=sys.stderr)
    return 1 if failures else 0


def cmd_dashboard(args, cfg: Config) -> int:
    import subprocess
    from pathlib import Path
    app = Path(__file__).parent / "dashboard" / "app.py"
    return subprocess.call(["streamlit", "run", str(app)])


def cmd_gui(args, cfg: Config) -> int:
    from .gui.app import run as run_gui
    return run_gui(cfg)


def _finish(run: TestRun, cfg: Config, client: ApiClient | None, *, authorised: bool,
           apk_file_name: str | None, client_name: str | None = None,
           fmt: str | None = None, output: str | None = None,
           fail_on: str | None = None, no_report: bool = False) -> int:
    """Rank, write the machine-readable output and judge --fail-on, then upload to the
    account (the only place the run is stored when signed in) and write the report file.
    The format output and the threshold verdict come first so a failed upload can never
    hide them. `client` is None for an offline run: nothing is uploaded or stored.
    Explaining findings with AI is a separate step (`bioaudit explain <scan-id>`) since
    it needs the scan id this function is what produces."""
    core.process_findings(run)

    # With --format and no --output stdout carries only the document, so a pipe stays valid.
    log = sys.stderr if fmt and not output else sys.stdout
    counts = run.counts()
    print(f"\nAssessed {run.package}: " +
          (", ".join(f"{k} {v}" for k, v in counts.items() if v) or "no findings"), file=log)
    _print_findings(run, log)

    if fmt:
        text = export.render(run, fmt)
        if output:
            parent = os.path.dirname(output)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"{fmt.upper()} written to: {output}", file=log)
        else:
            print(text)

    exceeded = export.exceeds(run, fail_on)
    if exceeded:
        print(f"Failing the build: a finding is at or above '{fail_on}'.", file=sys.stderr)

    upload_failed = None
    result = None
    if client is not None:
        try:
            result = client.upload_run(run, authorised=authorised,
                                       apk_file_name=apk_file_name, client_name=client_name)
        except ApiClientError as exc:
            upload_failed = exc

    # Written either way: a report the user can see with their own eyes is worth having
    # even when the upload — the run's only real storage — did not go through.
    path = None if no_report else core.write_report(run, cfg)

    if upload_failed is not None:
        print(f"\nCould not save this run to your account: {upload_failed}", file=sys.stderr)
        print("This run exists nowhere else; rerun this command once the problem is "
              "fixed (check your connection and `bioaudit whoami`).", file=sys.stderr)
        if path:
            print(f"A copy of the findings was written to: {path}", file=sys.stderr)
        return EXIT_THRESHOLD if exceeded else 1

    if result is not None:
        scan_id = result["scan"]["id"]
        print(f"\nSaved to your account: {scan_id}", file=log)
        retention = result.get("retention")
        if retention and retention.get("removed"):
            print(retention["message"], file=log)
        if path:
            print(f"Report written to: {path}", file=log)
        print(f"For plain-language explanations, run: bioaudit explain {scan_id}", file=log)
    elif path:
        print(f"Report written to: {path}", file=log)

    return EXIT_THRESHOLD if exceeded else 0


def _print_findings(run: TestRun, file=None) -> None:
    for f in run.ranked():
        print(f"  [{f.severity.label:8}] {f.title}  ({', '.join(f.owasp + f.masvs)})",
              file=file or sys.stdout)


def _add_ci_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "sarif", "markdown"],
                        help="also emit the run in a machine-readable format")
    parser.add_argument("--output", help="write --format output to this file, not stdout")
    parser.add_argument("--fail-on", choices=export.FAIL_ON_CHOICES,
                        help="exit 3 when any finding is at or above this severity")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bioaudit",
        description="Android biometric auth security tester (Mode B: black-box, no root)")
    p.add_argument("--config", help="path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    lg = sub.add_parser("login", help="sign in and remember the session")
    lg.add_argument("--email")
    lg.add_argument("--password", help="omit to be prompted (not echoed)")
    lg.add_argument("--server", help="override the configured API server")
    lg.add_argument("--no-remember", action="store_true",
                    help="do not save the session to disk (sign in for this command only)")
    lg.set_defaults(func=cmd_login)

    lo = sub.add_parser("logout", help="end the saved session")
    lo.set_defaults(func=cmd_logout)

    ac = sub.add_parser("accounts", help="list saved accounts")
    ac.set_defaults(func=cmd_accounts)

    sw = sub.add_parser("switch", help="switch to a saved account without a password")
    sw.add_argument("email")
    sw.set_defaults(func=cmd_switch)

    r = sub.add_parser("register", help="create an account")
    r.add_argument("--email", required=True)
    r.add_argument("--password", help="omit to be prompted (not echoed)")
    r.add_argument("--name", help="display name")
    r.add_argument("--organisation", help="create a team and make this account its admin")
    r.add_argument("--solo", action="store_true",
                   help="with --organisation: one-person team for freelancers, "
                        "so the owner can run scans")
    r.add_argument("--join-token", help="redeem a team invite instead of creating one")
    r.add_argument("--server", help="override the configured API server")
    r.add_argument("--no-remember", action="store_true",
                    help="do not save the session to disk (sign in for this command only)")
    r.add_argument("--university", metavar="ID",
                   help="your university's id from GET /api/universities; the email "
                        "must be one it issues (student accounts)")
    r.add_argument("--accept-terms", action="store_true",
                   help="accept the Terms of Use (docs/TERMS.md) without being prompted")
    r.set_defaults(func=cmd_register)

    w = sub.add_parser("whoami", help="show who is currently signed in")
    w.set_defaults(func=cmd_whoami)

    v = sub.add_parser("verify-student",
                       help="confirm your university email to unlock scanning")
    v.add_argument("--code", help="the six-digit code from the email (prompted if omitted)")
    v.add_argument("--resend", action="store_true", help="email a new code first")
    v.set_defaults(func=cmd_verify_student)

    s = sub.add_parser("scan-apk", help="static analysis of an APK (no device)")
    s.add_argument("apk")
    s.add_argument("--client", help="client name to record in the authorisation record")
    s.add_argument("--offline", action="store_true",
                   help="no sign-in and no upload (also BIOAUDIT_OFFLINE=1); for CI")
    s.add_argument("--no-report", action="store_true",
                   help="do not write the HTML report under reports/")
    _add_ci_flags(s)
    s.set_defaults(func=cmd_scan_apk)

    ci = sub.add_parser("init-ci", help="write a GitHub Actions workflow that scans your APK")
    ci.add_argument("--apk", help="APK path in your repo (auto-detected under build/outputs/apk)")
    ci.add_argument("--fail-on", choices=export.FAIL_ON_CHOICES, default="high")
    ci.add_argument("--dir", default=".", help="repository root (default: current directory)")
    ci.set_defaults(func=cmd_init_ci)

    a = sub.add_parser("assess", help="full black-box assessment on a connected device")
    a.add_argument("--package", required=True)
    a.add_argument("--apk", help="APK on disk (enables the IPC oracle + static analysis)")
    a.add_argument("--i-am-authorized", action="store_true",
                   help="confirm you own or are authorized to test this app")
    a.add_argument("--client", help="client name to record in the authorisation record")
    _add_ci_flags(a)
    a.set_defaults(func=cmd_assess)

    e = sub.add_parser("explain", help="ask the server to explain a saved run's findings")
    e.add_argument("scan_id")
    e.set_defaults(func=cmd_explain)

    d = sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    d.set_defaults(func=cmd_dashboard)

    g = sub.add_parser("gui", help="launch the PySide6 desktop app")
    g.set_defaults(func=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    return args.func(args, cfg)
