"""Make an existing account an administrator, from the server.

    pipenv run python scripts/make_admin.py you@company.com

For when EMAIL_VERIFICATION_REQUIRED is off: ADMIN_EMAILS then cannot grant
administration (an unconfirmed address proves nothing), so the person who runs
the server does it here. Having access to the server and its database file is
the proof. Reads APP_DB_PATH from the environment / .env like the app does.
Use --remove to take it away again.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so pp imports

from app.accounts import service as accounts
from app.accounts import store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email")
    parser.add_argument("--remove", action="store_true", help="remove administration instead")
    args = parser.parse_args()

    account = accounts.account_by_email(accounts.normalise_email(args.email))
    if account is None:
        print(f"No account for {args.email}. Sign up first, then run this again.")
        return 1
    with store.write() as conn:
        conn.execute("UPDATE users SET is_admin = ?, admin_set_by = 'admin' WHERE user_id = ?",
                     (0 if args.remove else 1, account.user_id))
    print(f"{account.email} is {'no longer' if args.remove else 'now'} an administrator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
