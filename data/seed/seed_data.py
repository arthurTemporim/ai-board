"""Demo data: 2 boards, 30 B2B SaaS feature requests, and the ground truth for evals.

Card ids are assigned 1..30 in the order of CARDS when seeding into an empty database, which is
what labels.json refers to. No CardLink/Problem rows are seeded: duplicates and groupings are
for the AI workflow to find, and labels.json is what it is measured against.
"""

from __future__ import annotations

BOARDS = [
    {"name": "Core Platform", "description": "Requests for the main web application."},
    {
        "name": "Integrations & Mobile",
        "description": "Requests for integrations, the public API and the mobile app.",
    },
]

USERS = [
    ("Alice Martin", "product_manager"),
    ("Bruno Costa", "product_manager"),
    ("Chloe Nguyen", "customer_success"),
    ("Dan Okafor", "sales"),
    ("Elena Petrov", "engineer"),
    ("Farid Haddad", "support"),
    ("Grace Liu", "designer"),
    ("Hugo Silva", "customer_success"),
]

TAGS = {
    0: [
        ("security", "#DC2626"),
        ("compliance", "#7C3AED"),
        ("reporting", "#2563EB"),
        ("ux", "#DB2777"),
        ("enterprise", "#059669"),
        ("quick-win", "#F59E0B"),
        ("admin", "#0891B2"),
    ],
    1: [
        ("integrations", "#2563EB"),
        ("mobile", "#DB2777"),
        ("api", "#0891B2"),
        ("notifications", "#F59E0B"),
        ("automation", "#7C3AED"),
        ("enterprise", "#059669"),
        ("quick-win", "#F59E0B"),
    ],
}

# Each card: key (stable slug), board (index into BOARDS), column, title, description,
# requester, tags, votes (how many of USERS voted), progress, age_days,
# comments [(user index, text)], feedback [(source, quote, customer_ref)].
CARDS: list[dict] = [
    # ---- Board 0: Core Platform --------------------------------------------------------
    {
        "key": "sso-saml", "board": 0, "column": "Planned",
        "title": "Support SAML single sign-on",
        "description": "Our IT team requires SAML 2.0 SSO before we can roll the product out beyond the pilot group.",
        "requester": "customer", "tags": ["security", "enterprise"], "votes": 7, "progress": 0, "age": 71,
        "comments": [(0, "Top enterprise blocker in three open deals."), (4, "Scoping: we can reuse the session layer, about 3 weeks.")],
        "feedback": [("Sales call", "We cannot sign the contract until SSO is in place.", "Northwind Logistics")],
    },
    {
        "key": "sso-okta", "board": 0, "column": "Triaged",
        "title": "Let our employees log in with their Okta accounts",
        "description": "Nobody wants to manage another password. All 400 of our staff are in Okta already.",
        "requester": "customer", "tags": ["security"], "votes": 4, "progress": 0, "age": 55,
        "comments": [(2, "Same ask as two other accounts this month.")],
        "feedback": [("Support ticket", "Our admins keep getting password reset requests for your app.", "Brightside Health")],
    },
    {
        "key": "sso-azure", "board": 0, "column": "Inbox",
        "title": "Azure AD / Entra sign-in for our whole org",
        "description": "We are a Microsoft shop. Please let users authenticate with their work Microsoft account.",
        "requester": "partner", "tags": ["enterprise"], "votes": 3, "progress": 0, "age": 12,
        "comments": [],
        "feedback": [("Partner QBR", "Our joint customers on Microsoft 365 ask for this constantly.", "Contoso Reseller")],
    },
    {
        "key": "csv-export", "board": 0, "column": "In Progress",
        "title": "Export report tables to CSV",
        "description": "Add an Export to CSV button on every report table, respecting the current filters.",
        "requester": "customer", "tags": ["reporting", "quick-win"], "votes": 6, "progress": 60, "age": 80,
        "comments": [(4, "Backend endpoint is done, UI button in review."), (1, "Keep the column order from the screen.")],
        "feedback": [("NPS survey", "Love the reports, but I have to copy and paste everything by hand.", "Acme Retail")],
    },
    {
        "key": "csv-excel", "board": 0, "column": "Triaged",
        "title": "Get dashboard numbers into Excel for my monthly review",
        "description": "Every month I rebuild the same spreadsheet from screenshots of your dashboard. There has to be a better way.",
        "requester": "customer", "tags": ["reporting"], "votes": 3, "progress": 0, "age": 33,
        "comments": [(2, "Likely the same need as the CSV export request.")],
        "feedback": [("Support ticket", "My finance team wants the raw numbers in a spreadsheet.", "Harbor Finance")],
    },
    {
        "key": "dark-mode", "board": 0, "column": "Under Review",
        "title": "Dark mode",
        "description": "Please add a dark color theme.",
        "requester": "customer", "tags": ["ux"], "votes": 5, "progress": 0, "age": 64,
        "comments": [(6, "Design tokens are ready; needs an audit of chart colors.")],
        "feedback": [],
    },
    {
        "key": "dark-night", "board": 0, "column": "Inbox",
        "title": "A night theme, the bright UI is tiring for our late-shift operators",
        "description": "Our operations team works overnight in a dim room and the white background is painful after a few hours.",
        "requester": "customer", "tags": ["ux"], "votes": 2, "progress": 0, "age": 9,
        "comments": [],
        "feedback": [("Customer interview", "By 3am the white screen is blinding.", "Vantage Operations")],
    },
    {
        "key": "audit-log", "board": 0, "column": "Planned",
        "title": "Audit log of admin actions",
        "description": "Record admin actions (user changes, permission changes, exports) in a searchable log.",
        "requester": "customer", "tags": ["security", "compliance", "admin"], "votes": 5, "progress": 0, "age": 60,
        "comments": [(0, "Needed for the ISO 27001 checklist too."), (4, "Append-only table plus a viewer page.")],
        "feedback": [("Security questionnaire", "Do you provide an audit trail of administrative actions?", "Globex Bank")],
    },
    {
        "key": "audit-history", "board": 0, "column": "Inbox",
        "title": "Need a history of who changed what, for our SOC 2 audit",
        "description": "Our auditors ask who changed which setting and when. Today we cannot answer that.",
        "requester": "customer", "tags": ["compliance"], "votes": 3, "progress": 0, "age": 6,
        "comments": [],
        "feedback": [("Support ticket", "The auditor rejected our evidence because we cannot show change history.", "Initech Payments")],
    },
    {
        "key": "scheduled-reports", "board": 0, "column": "Under Review",
        "title": "Email reports automatically every Monday",
        "description": "Managers want the weekly report in their inbox without logging in.",
        "requester": "customer", "tags": ["reporting"], "votes": 4, "progress": 0, "age": 41,
        "comments": [(1, "Related to exports but it is about delivery, not the file format.")],
        "feedback": [("Customer interview", "My VP never logs in; she reads email.", "Acme Retail")],
    },
    {
        "key": "custom-roles", "board": 0, "column": "Triaged",
        "title": "Custom permission roles beyond Admin and Member",
        "description": "We need roles such as Billing-only or Read-only auditor with specific permissions.",
        "requester": "customer", "tags": ["security", "admin", "enterprise"], "votes": 6, "progress": 0, "age": 49,
        "comments": [(3, "Asked for by four prospects during evaluations.")],
        "feedback": [("Sales call", "Giving everyone Admin is a non-starter for us.", "Northwind Logistics")],
    },
    {
        "key": "vague-dashboard", "board": 0, "column": "Inbox",
        "title": "Improve the dashboard",
        "description": "It could be better.",
        "requester": "internal", "tags": [], "votes": 1, "progress": 0, "age": 4,
        "comments": [(0, "Who raised this? Needs specifics before triage.")],
        "feedback": [],
    },
    {
        "key": "vague-managers", "board": 0, "column": "Inbox",
        "title": "Make the app better for managers",
        "description": "Managers have told us they find it hard to use. Not sure what exactly.",
        "requester": "internal", "tags": [], "votes": 0, "progress": 0, "age": 2,
        "comments": [],
        "feedback": [],
    },
    {
        "key": "two-factor", "board": 0, "column": "Shipped",
        "title": "Two-factor authentication for all users",
        "description": "Support authenticator apps as a second factor, with an option for admins to enforce it.",
        "requester": "customer", "tags": ["security"], "votes": 8, "progress": 100, "age": 88,
        "comments": [(4, "Shipped in the last release; enforcement setting is under Security.")],
        "feedback": [("NPS survey", "2FA finally. Thank you.", "Brightside Health")],
    },
    {
        "key": "custom-fields", "board": 0, "column": "Under Review",
        "title": "Custom fields on records",
        "description": "Let admins add their own text, number and dropdown fields to records.",
        "requester": "customer", "tags": ["admin"], "votes": 5, "progress": 0, "age": 37,
        "comments": [(6, "Needs design for how fields appear in reports.")],
        "feedback": [("Customer interview", "Every team tracks something different; we keep it in a side spreadsheet.", "Vantage Operations")],
    },
    {
        "key": "saved-filters", "board": 0, "column": "Declined",
        "title": "Save and share filtered views",
        "description": "Let users save a filter combination and share the link with teammates.",
        "requester": "customer", "tags": ["ux"], "votes": 2, "progress": 0, "age": 75,
        "comments": [(0, "Declined for now: URL-based filters cover most of this. Revisit with custom fields.")],
        "feedback": [],
    },
    {
        "key": "keyboard-shortcuts", "board": 0, "column": "In Progress",
        "title": "Keyboard shortcuts for power users",
        "description": "Shortcuts for creating, searching and navigating between records.",
        "requester": "internal", "tags": ["ux", "quick-win"], "votes": 2, "progress": 30, "age": 28,
        "comments": [(6, "Shortcut cheat sheet modal is designed.")],
        "feedback": [],
    },
    {
        "key": "multi-language", "board": 0, "column": "Planned",
        "title": "Localize the UI in German and French",
        "description": "Our EU partners need the interface in German and French to sell to their customers.",
        "requester": "partner", "tags": ["ux", "enterprise"], "votes": 4, "progress": 0, "age": 52,
        "comments": [(1, "Waiting for the string extraction work before we can start.")],
        "feedback": [("Partner QBR", "Two deals in DACH stalled on the missing German UI.", "Contoso Reseller")],
    },
    # ---- Board 1: Integrations & Mobile ---------------------------------------------------
    {
        "key": "slack-alerts", "board": 1, "column": "Planned",
        "title": "Slack integration for alerts",
        "description": "Send alerts and status changes to a Slack channel.",
        "requester": "customer", "tags": ["integrations", "notifications"], "votes": 7, "progress": 0, "age": 66,
        "comments": [(4, "Will build on the notification preferences model."), (0, "Most requested integration.")],
        "feedback": [("NPS survey", "We live in Slack; email alerts get ignored.", "Acme Retail")],
    },
    {
        "key": "team-chat", "board": 1, "column": "Inbox",
        "title": "Post notifications to our team chat channel",
        "description": "When a high priority item changes, our support team should see it in the channel they already watch all day.",
        "requester": "customer", "tags": ["notifications"], "votes": 3, "progress": 0, "age": 8,
        "comments": [],
        "feedback": [("Support ticket", "We missed an urgent update because nobody checks the email inbox.", "Harbor Finance")],
    },
    {
        "key": "webhooks", "board": 1, "column": "Triaged",
        "title": "Webhooks for record events",
        "description": "Call a URL we configure when records are created, updated or deleted, so we can trigger our own systems.",
        "requester": "partner", "tags": ["api", "automation"], "votes": 5, "progress": 0, "age": 45,
        "comments": [(4, "Related to chat alerts but general purpose: needs retries and signing.")],
        "feedback": [("Partner QBR", "Webhooks would let us build our connector without polling.", "Contoso Reseller")],
    },
    {
        "key": "mobile-push", "board": 1, "column": "Under Review",
        "title": "Push notifications on the mobile app",
        "description": "Notify me on my phone when something assigned to me changes.",
        "requester": "customer", "tags": ["mobile", "notifications"], "votes": 6, "progress": 0, "age": 58,
        "comments": [(6, "Needs a decision on which events are worth a push.")],
        "feedback": [("App store review", "Great app but I never know when something needs me.", "Anonymous")],
    },
    {
        "key": "mobile-offline", "board": 1, "column": "Inbox",
        "title": "Offline mode on mobile for field technicians",
        "description": "Technicians work in basements and rural sites without signal and need to read and update records offline.",
        "requester": "customer", "tags": ["mobile", "enterprise"], "votes": 4, "progress": 0, "age": 15,
        "comments": [],
        "feedback": [("Customer interview", "Half our jobs are in places with no reception.", "Vantage Operations")],
    },
    {
        "key": "api-keys", "board": 1, "column": "In Progress",
        "title": "Self-service API keys with scoped permissions",
        "description": "Let admins create and revoke API keys limited to specific resources.",
        "requester": "partner", "tags": ["api", "enterprise"], "votes": 5, "progress": 50, "age": 39,
        "comments": [(4, "Key creation and hashing done; scopes UI next.")],
        "feedback": [],
    },
    {
        "key": "rate-limit-visibility", "board": 1, "column": "Triaged",
        "title": "Show API rate-limit usage in settings",
        "description": "Display current usage against the rate limit so integrators can see when they are close.",
        "requester": "internal", "tags": ["api"], "votes": 2, "progress": 0, "age": 22,
        "comments": [(5, "Support gets a ticket about 429 errors every week.")],
        "feedback": [],
    },
    {
        "key": "email-to-card", "board": 1, "column": "Shipped",
        "title": "Create requests by forwarding an email",
        "description": "Forward an email to a board address and get a new request in the Inbox column.",
        "requester": "customer", "tags": ["automation", "quick-win"], "votes": 3, "progress": 100, "age": 84,
        "comments": [(4, "Live for all plans.")],
        "feedback": [("NPS survey", "Forwarding emails into the board saves me an hour a week.", "Initech Payments")],
    },
    {
        "key": "calendar-sync", "board": 1, "column": "Declined",
        "title": "Sync due dates to Google Calendar",
        "description": "Show due dates as events in my Google Calendar.",
        "requester": "customer", "tags": ["integrations"], "votes": 1, "progress": 0, "age": 70,
        "comments": [(0, "Declined: low demand and a high maintenance cost for two-way sync.")],
        "feedback": [],
    },
    {
        "key": "jira-import", "board": 1, "column": "Under Review",
        "title": "Import existing tickets from Jira",
        "description": "We are switching from Jira and do not want to lose our backlog history.",
        "requester": "customer", "tags": ["integrations", "enterprise"], "votes": 4, "progress": 0, "age": 31,
        "comments": [(3, "Comes up in most migration conversations.")],
        "feedback": [("Sales call", "Migrating 6,000 tickets by hand is not realistic.", "Globex Bank")],
    },
    {
        "key": "github-link", "board": 1, "column": "Inbox",
        "title": "Link pull requests to records",
        "description": "Show the status of linked GitHub pull requests on a record.",
        "requester": "customer", "tags": ["integrations"], "votes": 2, "progress": 0, "age": 11,
        "comments": [],
        "feedback": [],
    },
    {
        "key": "no-code-connect", "board": 1, "column": "Inbox",
        "title": "Connect to no-code automation tools",
        "description": "Would be nice to use this from Zapier-style tools.",
        "requester": "unknown", "tags": ["automation"], "votes": 1, "progress": 0, "age": 3,
        "comments": [],
        "feedback": [],
    },
]

# ---- ground truth (keys, resolved to ids by position in CARDS) ---------------------------
DUPLICATE_GROUPS = {
    "sso": ["sso-saml", "sso-okta", "sso-azure"],
    "csv-export": ["csv-export", "csv-excel"],
    "dark-mode": ["dark-mode", "dark-night"],
    "audit-trail": ["audit-log", "audit-history"],
    "chat-notifications": ["slack-alerts", "team-chat"],
}

# Related but NOT duplicates: same area, different need.
RELATED_PAIRS = [
    ("csv-export", "scheduled-reports"),
    ("audit-log", "custom-roles"),
    ("slack-alerts", "webhooks"),
]

VAGUE = ["vague-dashboard", "vague-managers"]

# The underlying customer problem each request belongs to (for problem-grouping evals).
PROBLEM_GROUPS = {
    "Enterprise buyers cannot meet their security and compliance requirements": [
        "sso-saml", "sso-okta", "sso-azure", "audit-log", "audit-history", "custom-roles", "two-factor",
    ],
    "Customers cannot get their data out to share or review it elsewhere": [
        "csv-export", "csv-excel", "scheduled-reports",
    ],
    "Teams miss important changes because alerts do not reach where they work": [
        "slack-alerts", "team-chat", "webhooks", "mobile-push",
    ],
}


def card_ids() -> dict[str, int]:
    return {card["key"]: index for index, card in enumerate(CARDS, start=1)}


def build_labels() -> dict:
    ids = card_ids()
    groups = [
        {"name": name, "keys": keys, "card_ids": [ids[k] for k in keys]}
        for name, keys in DUPLICATE_GROUPS.items()
    ]
    duplicate_pairs = sorted(
        [a, b]
        for group in groups
        for i, a in enumerate(sorted(group["card_ids"]))
        for b in sorted(group["card_ids"])[i + 1:]
    )
    return {
        "version": 1,
        "note": "Ground truth for evals. card ids match a fresh `make seed`. Generated by "
        "`python -m data.seed.seed --write-labels`; do not edit by hand.",
        "cards": {
            str(ids[c["key"]]): {"key": c["key"], "title": c["title"], "board": BOARDS[c["board"]]["name"]}
            for c in CARDS
        },
        "duplicate_groups": groups,
        "duplicate_pairs": duplicate_pairs,
        "related_pairs": sorted(sorted([ids[a], ids[b]]) for a, b in RELATED_PAIRS),
        "vague_card_ids": sorted(ids[k] for k in VAGUE),
        "problem_groups": [
            {"problem": name, "keys": keys, "card_ids": sorted(ids[k] for k in keys)}
            for name, keys in PROBLEM_GROUPS.items()
        ],
    }
