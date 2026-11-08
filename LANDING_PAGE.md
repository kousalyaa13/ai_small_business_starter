# Northstar landing website

The public homepage (`/`) introduces Northstar to small-business owners. The existing simple decision dashboard is available at `/dashboard`, and the detailed consultation agent remains at `/consult`. A separate `/solutions` page explains product capabilities and limits of source verification.

## Update content without editing layouts

All public statistics, challenge cards, industry examples, capability descriptions and independent video links live in **`data/landing.json`**. Edit the relevant array and restart/redeploy the Flask app. Keep statistics attributed to specific reports, clearly state the survey population, and update the year and original source URL whenever a figure changes.

The stories section links to independently published TED talks. **It does not claim these speakers endorse Northstar.** Don't add invented business testimonials. If real owners later provide pilot feedback and consent to publication, add a separately labeled testimonial section with their approved wording.

### Core routes

- `/` — public landing page, interactive challenge picker.
- `/solutions` — all currently demonstrated capabilities and honest verification explanation.
- `/dashboard?challenge=finance` — original quick intake with an optional preselected challenge.
- `/consult?challenge=finance` — original consultation intake prefilled with challenge wording.
- `/consult?industry=retail` — original consultation intake prefilled with industry wording.

The challenge selector **prefills** rather than auto-submitting sensitive business details. Users remain in control of what they enter and approve. All existing JSON APIs and agent routes are unchanged.

### UI assets

- `templates/landing.html` and `templates/solutions.html`
- `static/landing.css` and `static/landing.js`
- `data/landing.json`

Photos use Unsplash URLs and require network access. Google Fonts fall back to system fonts if blocked. The site remains usable without photos or fonts.

### Research and verification policy

Survey statistics in the initial page are from page 21 of the [2025 U.S. Chamber Empowering Small Business report](https://www.uschamber.com/assets/documents/Empowering-Small-Business-Report-2025.pdf), specifically respondents **not currently using AI**. The original prototype catalog itself explicitly requires verification. Northstar's estimate of tool fit, projected value, and compatibility must not be represented as independently validated outcomes.

### Smoke tests

Run `python -m unittest discover -s tests -v` (requires dependencies from `requirements.txt`). Also visit the landing page on desktop and mobile, tab through challenge cards, and check `/consult?challenge=admin` before deployment.
