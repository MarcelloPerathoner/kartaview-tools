#!/usr/bin/env python3

"""
Get KartaView authentication for an OSM account.

You must have an OSM account before running this.  This script will ask you to login into OSM and
grant privileges to this app.

KartaView authentication tokens typically do not expire so you need to run this only once.
"""

from typing import Any, Dict
import webbrowser

import requests
import requests_oauthlib as oauthlib

import kartaview_tools as kt


OSM_REQUEST_TOKEN_URL = "https://www.openstreetmap.org/oauth/request_token"
OSM_BASE_AUTHORIZATION_URL = "https://www.openstreetmap.org/oauth/authorize"
OSM_ACCESS_TOKEN_URL = "https://www.openstreetmap.org/oauth/access_token"
OSM_CLIENT_KEY = "rBWV8Eaottv44tXfdLofdNvVemHOL62Lsutpb9tw"
OSM_CLIENT_SECRET = "rpmeZIp49sEjjcz91X9dsY0vD1PpEduixuPy8T6S"

OSC_LOGIN_URL = "https://api.openstreetcam.org/auth/openstreetmap/client_auth"


def osm_oauth() -> Dict[str, str]:
    """Do the OSM oauth dance.

    This directs the user to go to the OSM site and authorize this app.
    """
    oauth = oauthlib.OAuth1Session(OSM_CLIENT_KEY, client_secret=OSM_CLIENT_SECRET)
    fetch_response = oauth.fetch_request_token(OSM_REQUEST_TOKEN_URL)
    # {
    #    "oauth_token": "Z6eEdO8MOmk394WozF5oKyuAv855l4Mlqo7hhlSLik",
    #    "oauth_token_secret": "Kd75W4OQfb2oJTV0vzGzeXftVAwgMnEK9MumzYcM"
    # }
    resource_owner_key = fetch_response.get("oauth_token")
    resource_owner_secret = fetch_response.get("oauth_token_secret")

    authorization_url = oauth.authorization_url(OSM_BASE_AUTHORIZATION_URL)
    print(
        f"""
This script should have opened a browser window.
Please go to the browser, login with your OSM account, and authorize this app.
You need an OSM account to use this app.
If no browser window should have opened, please do it yourself and go to this url:

{authorization_url}
"""
    )

    webbrowser.open_new_tab(authorization_url)

    input("Then come back here and press ENTER.")

    # looks like we don't need a verifier
    # redirect_response = input("Then paste the full redirect URL here: ")
    # oauth_response = oauth.parse_authorization_response(redirect_response)
    # {
    #    "oauth_token": "Z6eEdO8MOmk394WozF5oKyuAv855l4Mlqo7hhlSLik",
    #    "oauth_verifier": "sdflk3450FASDLJasd2349dfs"
    # }
    # verifier = oauth_response.get("oauth_verifier")
    try:
        oauth = oauthlib.OAuth1Session(
            OSM_CLIENT_KEY,
            client_secret=OSM_CLIENT_SECRET,
            resource_owner_key=resource_owner_key,
            resource_owner_secret=resource_owner_secret,
            verifier=" ",
        )

        return oauth.fetch_access_token(OSM_ACCESS_TOKEN_URL)
        # {
        #    "oauth_token": "6253282-eWudHldSbIaelX7swmsiHImEL4KinwaGloHANdrY",
        #    "oauth_token_secret": "2EEfA6BG3ly3sR3RjE0IBSnlQu4ZrUzPiYKmrkVU"
        # }
    except oauthlib.oauth1_session.TokenRequestDenied as e:
        raise kt.KartaviewError("You have not authorized this app.") from e


def kv_auth(osm_oauth: Dict[str, str]) -> Dict[str, Any]:
    """Do the KartaView auth dance.

    This only requires a valid OSM oauth token.
    """
    try:
        # same as osm_oauth with gratuitous idiotic renaming of parameters
        silly_osm_oauth = {
            "request_token": osm_oauth["oauth_token"],
            "secret_token": osm_oauth["oauth_token_secret"],
        }

        r = requests.post(OSC_LOGIN_URL, data=silly_osm_oauth)
        r.raise_for_status()

        return {
            "osm": osm_oauth,
            "kartaview": r.json()["osv"],
        }

    except (requests.RequestException, KeyError) as e:
        raise kt.KartaviewError("Login Error") from e


def main():
    """Run this."""
    kt.write_config_file(kv_auth(osm_oauth()))


if __name__ == "__main__":
    main()
