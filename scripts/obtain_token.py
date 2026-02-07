#!/usr/bin/env python3
"""
One-time script to obtain a Google OAuth 2.0 refresh token for the Photos Library API.

Run from project root. Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in environment
(or pass as arguments). Opens a browser for sign-in; prints the refresh token to copy into .env.
"""

import argparse
import os
import sys

# Add project root so we can use bot.config if needed; script runs standalone with env vars
GOOGLE_PHOTOS_SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Obtain Google OAuth refresh token for Photos Library API"
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        help="OAuth 2.0 client ID (default: GOOGLE_CLIENT_ID)",
    )
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        help="OAuth 2.0 client secret (default: GOOGLE_CLIENT_SECRET)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for OAuth redirect (default: 8080)",
    )
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Error: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the environment,\n"
            "or pass --client-id and --client-secret.",
            file=sys.stderr,
        )
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "Error: Install google-auth-oauthlib (pip install google-auth-oauthlib)",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "redirect_uris": [f"http://localhost:{args.port}/"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, GOOGLE_PHOTOS_SCOPES)
    print(f"Opening browser for sign-in (redirect http://localhost:{args.port}/) ...")
    credentials = flow.run_local_server(port=args.port, open_browser=True)

    if not credentials.refresh_token:
        print("Error: No refresh token in response.", file=sys.stderr)
        return 1

    print("\n--- Add this to your .env file ---")
    print(f"GOOGLE_REFRESH_TOKEN={credentials.refresh_token}")
    print("-----------------------------------\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
