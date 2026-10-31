# Profiles, sign-in and deck ownership

## What's there today

- **Profiles.** Each person has a profile: a name, optionally an email, and admin or not.
  The first profile ever created is the admin.
- **Sign-in required everywhere.** Every page except `/login` needs a signed-in profile.
  A signed cookie remembers you for 30 days.
- **Deck ownership.** Every deck belongs to one profile. Its owner chooses who can see it:
  - **Only me** (the default for new, imported and copied decks)
  - **Everyone on this server**, read-only. Others see it under "Shared with you" and
    can **Copy to my decks** to make their own editable version.
- **Admins** can add profiles (Profile page, or the CLI). Admins **cannot** see other
  people's private decks.

## Signing in: the profile picker, or Cloudflare Access

**Profile picker (default).** You sign in by clicking your profile on the "Who's using
TCG Necropolis?" page. There are no passwords, so **anyone who can reach the page can
sign in as anyone**. That's fine on a home network with people you trust. Don't put it
on the internet like this.

**Cloudflare Access (for internet access).** Set `TCGNECRO_CF_ACCESS_TEAM` and
`TCGNECRO_CF_ACCESS_AUD` (setup: [DOCKER.md](DOCKER.md), "Internet access with
Cloudflare Tunnel and Access"). Cloudflare signs people in before they reach the app and
sends a signed token with every request. The app checks the token (signed with your
team's keys, for this application, not expired) and signs the person in as the profile
whose **email** matches; there's no picker.

- Each person needs their email on their profile (admins edit them under Profile →
  Profiles, or `tcgnecro user email`), and on the Access policy.
- A verified email with no profile gets a page saying to ask an admin.
- With no profiles yet, the first person through Access creates the admin profile, with
  their email.
- Requests without a valid token are refused, so the app can't be reached around
  Cloudflare (for example on its port on your network).
- Your own email is fixed on your profile page in this mode, so you can't lock yourself
  out by mistyping it; change it with `tcgnecro user email` if you must.
- **Sign out** ends the Cloudflare session (`/cdn-cgi/access/logout`).

Google/GitHub sign-in inside the app (Authlib) remains an option for setups without
Cloudflare; the profile's `identities` field is reserved for it.

## First run

1. Start the server and open it in a browser.
2. With no profiles yet, you get a **Welcome** page: enter your name. That profile is the
   admin, and it takes ownership of every deck made before profiles existed.
3. Add friends' profiles on **Profile → Add a profile** (or `tcgnecro user add`).
   They then pick their name on the sign-in page.

## Managing profiles from the command line

```bash
tcgnecro user list                         # profiles, roles, how many decks each owns
tcgnecro user add "Friend" [--email E] [--admin]
tcgnecro user rename "Friend" "Alex"
tcgnecro user email "Alex" alex@example.com    # the email Cloudflare Access signs them in with
tcgnecro user admin "Alex"                 # make an admin; --off to undo
tcgnecro user remove "Alex" --decks-to "Brian"
```

- Removing a profile that owns decks requires `--decks-to`, so no deck is ever orphaned.
- The last admin can't be removed or demoted.
- If someone loses access (a wrong email with Cloudflare Access), this CLI is the recovery path.

## Where it's stored

| File | What |
|---|---|
| `data/users.json` | Profiles, including each profile's settings (`prefs`). Owned packs are `prefs.owned_packs = {game: [pack ids]}`; the chosen time zone is `prefs.timezone` (absent = the device's). User data: back it up with `data/decks/` |
| `data/secret.key` | Signs the session cookie. Generated on first run. Deleting it signs everyone out |
| `data/decks/*.json` | Each deck's `owner` (profile id) and `visibility` |

`index.db` holds none of this, so it is still safe to delete and rebuild.

Decks saved before profiles existed have no owner (deck format 1). Until a profile
claims them they belong to the admins, and the first profile created claims them
automatically.

## Settings for running it for others

| Environment variable | Default | Purpose |
|---|---|---|
| `TCGNECRO_SECURE_COOKIES=1` | off | Only send the session cookie over HTTPS. Turn on when serving over HTTPS |
| `TCGNECRO_CF_ACCESS_TEAM`, `TCGNECRO_CF_ACCESS_AUD` | off | Sign in through Cloudflare Access (above) |
| `TCGNECRO_SOURCE_URL` | this project's GitHub | Where the footer's "Source code" link points. The AGPL requires it to lead to the code you're actually running |

## How it works (for code changes)

| Piece | Where |
|---|---|
| Profile store (create/update/remove, the first profile claims decks) | `tcgnecro/users.py` |
| Session cookie, sign-in-required middleware, `/login`, `/logout`, `/profile` | `tcgnecro/auth.py` |
| Who may see or edit a deck | `Deck.viewable_by` / `Deck.editable_by` in `deckbuilder/model.py` |
| Enforcement | `_load(request, deck_id, edit=...)` in `deckbuilder/routes.py`: 404 if you can't see a deck, 403 if you can see but not edit it |
| Shared layout, user menu, footer | `tcgnecro/templates/base.html` |

HTMX requests from a page whose session has expired get an `HX-Redirect` to `/login`
instead of a login page swapped into part of the page.

Tests: `tests/test_accounts.py`.
