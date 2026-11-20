"""Individual and business onboarding canvases. Stories trace a fitted path."""

from __future__ import annotations

from kai.engine.graphlib import compile_paths, layout
from kai.engine.topup import eval_config

INDIVIDUAL = [
    ("chat_request", "source", "chat_request", "On demand from chat"),
    ("onboarding_started", "trigger", None, "a new application, empty"),
    ("ask_signup_method", "question", None, "1 · how you sign up"),
    ("signup_method_given", "user_response", None, "email, or Google"),
    ("google_sign_in", "fetch", None, "1a · Google returns a verified email"),
    ("ask_email_password", "question", None, "1a · email and password"),
    ("email_given", "user_response", None, "email and password"),
    ("ask_email_code", "question", None, "1b · the code we emailed"),
    ("email_verified", "user_response", None, "code confirmed"),
    ("offer_passkey", "question", None, "1c · Face ID or a passkey"),
    ("passkey_choice", "user_response", None, "set up, or not now"),
    ("ask_residence", "question", None, "2 · country of residence"),
    ("residence_given", "user_response", None, "one of the listed countries"),
    ("residence_not_served", "stop", None, "typed a country not listed"),
    ("ask_basics", "question", None, "3 · name, date of birth, address"),
    ("basics_given", "user_response", None, "name, date of birth, address"),
    ("is_adult", "condition", None, "date of birth ≥ 18 years ago"),
    ("under_18", "stop", None, "friendly finish, see you at 18"),
    ("ask_source_of_funds", "question", None, "4 · where your money comes from"),
    ("sources_given", "user_response", None, "one or more sources"),
    ("classify_free_text", "fetch", None, "AI maps the text to a source"),
    ("business_income", "condition", None, "Self-employment or business income picked?"),
    ("ask_registered", "question", None, "4a · is the business registered?"),
    ("registration_given", "user_response", None, "registered, or not"),
    ("ask_account_use", "question", None, "4b · personal or business account?"),
    ("account_use_given", "user_response", None, "personal, or business"),
    ("switch_to_business", "handoff", None, "hands over to business onboarding"),
    ("ask_plans", "question", None, "5 · what you’ll use Kai for"),
    ("plans_given", "user_response", None, "one or more uses"),
    ("ask_volume", "question", None, "5a · how much a month"),
    ("volume_given", "user_response", None, "one band"),
    ("ask_citizenship", "question", None, "6 · citizenship"),
    ("citizenship_given", "user_response", None, "one country"),
    ("ask_identity_scan", "question", None, "6a · ID and selfie"),
    ("identity_captured", "user_response", None, "document + selfie uploaded"),
    ("verify_identity", "sumsub", None, "document check · face match"),
    ("scan_failed_review", "review", None, "a person checks the ID"),
    ("ask_us_person", "question", None, "7 · US person?"),
    ("us_answer", "user_response", None, "yes, or no"),
    ("popup_us_offer", "question_popup", None, "7a · popup · what Kai offers you"),
    ("w9_tin_given", "user_response_popup", None, "TIN, or later"),
    ("ask_tax", "question", None, "7b · tax residency and tax ID"),
    ("tax_given", "user_response", None, "residencies and TINs"),
    ("popup_confirm_application", "question_popup", None, "8 · popup · your application"),
    ("application_confirmed", "user_response_popup", None, "proceed, or edit answers"),
    ("popup_edit_answers", "question_popup", None, "8 · popup · edit your answers"),
    ("answers_edited", "user_response_popup", None, "the changed fields, old and new"),
    ("apply_edit_rerun_checks", "sumsub", None, "edit logged · checks and score re-run"),
    ("anything_to_clarify", "condition", None, "a name, address, citizenship or country flag?"),
    ("ask_clarify", "question", None, "8a · only what was flagged"),
    ("clarified", "user_response", None, "answers added to the record"),
    ("residence_evidence_needed", "condition", None, "residence and citizenship in different regions?"),
    ("ask_residence_evidence", "question", None, "8b · proof of address or permit"),
    ("evidence_uploaded", "user_response", None, "a document uploaded"),
    ("check_evidence", "sumsub", None, "document type · date · address match"),
    ("evidence_review", "review", None, "a person checks the evidence"),
    ("popup_pep", "question_popup", None, "9 · popup · political exposure"),
    ("pep_declared", "user_response_popup", None, "one or more, or none"),
    ("popup_declaration", "question_popup", None, "9a · popup · confirm and submit"),
    ("own_behalf_and_signed", "user_response_popup", None, "submitted, or not for them"),
    ("not_own_behalf", "stop", None, "only for yourself"),
    ("run_screening", "sumsub", None, "sanctions · PEP · adverse media"),
    ("sanctions_hit", "condition", None, "a confirmed sanctions match?"),
    ("cannot_open", "stop", None, "no reason given"),
    ("score_high", "condition", None, "High, or a flagged factor in High?"),
    ("open_after_approval", "review", None, "a person approves first"),
    ("open_account", "action", None, "individual · Tier 1 · products set"),
    ("issue_virtual_iban", "baas", None, "virtual account from the BaaS partner"),
    ("account_opened", "result", None, "everything gathered · welcome to Kai"),
    ("create_applicant", "sumsub", None, "from sign-up and basics"),
    ("sync_profile", "sumsub", None, "declared profile + Kai’s rating"),
    ("f1_residence", "score", None, "F1 · residence"),
    ("f2_sources", "score", None, "F2 · sources"),
    ("f3_intent", "score", None, "F3 · intent"),
    ("f6_citizenship", "score", None, "F6 · citizenship"),
    ("f4_f5_identity", "score", None, "F4 F5 · identity"),
    ("f7_us_person", "score", None, "F7 · US person"),
    ("f8_tax", "score", None, "F8 · tax"),
    ("rescore_after_edits", "score", None, "no edits · total 18"),
    ("f9_screening", "score", None, "F9 · screening"),
    ("score_total", "score", None, "Score 41.2 · Medium"),
]

BUSINESS = [
    ("chat_request", "source", "chat_request", "On demand from chat"),
    ("handoff_from_individual", "source", None, "Handed over from individual onboarding"),
    ("onboarding_started", "trigger", None, "a new business application"),
    ("ask_signup_method", "question", None, "1 · how you sign up"),
    ("signup_method_given", "user_response", None, "email, or Google"),
    ("google_sign_in", "fetch", None, "1a · Google returns a verified email"),
    ("ask_email_password", "question", None, "1a · email and password"),
    ("email_given", "user_response", None, "email and password"),
    ("ask_email_code", "question", None, "1b · the code we emailed"),
    ("email_verified", "user_response", None, "code confirmed"),
    ("offer_passkey", "question", None, "1c · Face ID or a passkey"),
    ("passkey_choice", "user_response", None, "set up, or not now"),
    ("ask_ownership_structure", "question", None, "2 · how is it owned?"),
    ("structure_given", "user_response", None, "owned by people, or a group"),
    ("structure_not_served", "stop", None, "friendly not-yet · notify me"),
    ("ask_company_country", "question", None, "3 · where it is registered"),
    ("company_country_given", "user_response", None, "one of the listed countries"),
    ("company_country_not_served", "stop", None, "not on the business list"),
    ("ask_registration_number", "question", None, "4 · registration number"),
    ("registration_number_given", "user_response", None, "number (and court)"),
    ("registry_lookup", "sumsub", None, "Sumsub KYB · creates the company applicant"),
    ("registry_in_scope", "condition", None, "active, and owned by people?"),
    ("company_not_active", "stop", None, "dissolved or not active"),
    ("corporate_shareholder", "stop", None, "a shareholder is a company · notify me"),
    ("popup_registry_card", "question_popup", None, "4a · popup · is this your company?"),
    ("registry_confirmed", "user_response_popup", None, "confirmed, or not right"),
    ("ask_whats_wrong", "question", None, "4b · wrong company, or outdated?"),
    ("registry_issue_given", "user_response", None, "wrong company, or outdated"),
    ("ask_registry_correction", "question", None, "4c · what has changed"),
    ("correction_declared", "user_response", None, "kept beside the registry value"),
    ("ask_entity_tax", "question", None, "4d · company tax and US status"),
    ("entity_tax_given", "user_response", None, "tax number or later · US status"),
    ("is_us_entity", "condition", None, "a US entity?"),
    ("flag_us_entity", "action", None, "noted · review before opening"),
    ("ask_activity", "question", None, "5 · what the company does"),
    ("activity_given", "user_response", None, "one sector"),
    ("classify_activity", "fetch", None, "AI maps the sentence to a sector"),
    ("high_risk_sector", "condition", None, "a high-risk or restricted sector?"),
    ("add_review_note", "action", None, "one note · documents asked in the review"),
    ("ask_trading_name_website", "question", None, "5a · trading name and website"),
    ("brand_given", "user_response", None, "name and site, or none"),
    ("ask_business_use", "question", None, "6 · what the account is for"),
    ("use_given", "user_response", None, "one or more uses"),
    ("ask_business_volume", "question", None, "6a · how much a month"),
    ("volume_given", "user_response", None, "one band"),
    ("ask_company_funds", "question", None, "6b · where the money comes from"),
    ("funds_given", "user_response", None, "one category"),
    ("ask_role", "question", None, "7 · your role"),
    ("role_given", "user_response", None, "a role"),
    ("match_registry_name", "fetch", None, "AI name-match against the registry"),
    ("role_conflict_review", "review", None, "a person checks the claim"),
    ("ask_authority_document", "question", None, "7a · power of attorney or resolution"),
    ("authority_uploaded", "user_response", None, "a document uploaded"),
    ("classify_authority_document", "fetch", None, "doc AI · which one, and do the names match?"),
    ("authority_review", "review", None, "a person checks the document"),
    ("ask_basics", "question", None, "8 · your name, date of birth, address"),
    ("basics_given", "user_response", None, "name, date of birth, address"),
    ("ask_citizenship", "question", None, "8a · citizenship"),
    ("citizenship_given", "user_response", None, "one country"),
    ("ask_identity_scan", "question", None, "8b · ID and selfie"),
    ("identity_captured", "user_response", None, "document + selfie uploaded"),
    ("verify_identity", "sumsub", None, "the applicant · linked to the company"),
    ("scan_failed_review", "review", None, "a person checks the ID"),
    ("ask_ownership", "question", None, "9 · who owns 25% or more"),
    ("ownership_given", "user_response", None, "just me, or others too"),
    ("ask_owner_details", "question", None, "9a · each owner, their email and share"),
    ("owners_given", "user_response", None, "owners and shares"),
    ("owner_is_company", "stop", None, "friendly not-yet · notify me"),
    ("shares_add_up", "condition", None, "shares add up, and match the registry?"),
    ("ask_fix_shares", "question", None, "9b · the shares don’t add up"),
    ("shares_fixed", "user_response", None, "corrected shares"),
    ("invite_owners", "sumsub", None, "each owner verifies on their own link"),
    ("popup_confirm_application", "question_popup", None, "10 · popup · your application"),
    ("application_confirmed", "user_response_popup", None, "proceed, or edit answers"),
    ("popup_edit_answers", "question_popup", None, "10 · popup · edit your answers"),
    ("answers_edited", "user_response_popup", None, "the changed fields, old and new"),
    ("apply_edit_rerun_checks", "sumsub", None, "edit logged · checks and score re-run"),
    ("popup_documents", "question_popup", None, "10a · popup · documents and speed-ups"),
    ("documents_given", "user_response_popup", None, "required, plus any optional"),
    ("check_company_documents", "sumsub", None, "doc AI · type, date, matches the registry"),
    ("popup_pep", "question_popup", None, "11 · popup · political exposure"),
    ("pep_declared", "user_response_popup", None, "one or more, or none"),
    ("popup_declaration", "question_popup", None, "11a · popup · sign and submit"),
    ("declared_and_signed", "user_response_popup", None, "submitted, or client money"),
    ("client_money_review", "review", None, "a person looks at the model"),
    ("run_screening", "sumsub", None, "company · applicant · owners"),
    ("sanctions_hit", "condition", None, "a confirmed sanctions match?"),
    ("cannot_open", "stop", None, "no reason given"),
    ("needs_a_person", "condition", None, "High, or a flag on the way?"),
    ("open_after_approval", "review", None, "a person approves first"),
    ("open_account", "action", None, "business · opening limits · products set"),
    ("issue_virtual_iban", "baas", None, "virtual account in the company’s name"),
    ("account_opened", "result", None, "everything gathered · welcome to Kai"),
    ("sync_profile", "sumsub", None, "declared profile + Kai’s rating"),
    ("bf1_company_country", "score", None, "BF1 · company country"),
    ("bf2_registry", "score", None, "BF2 · registry standing"),
    ("bf11_entity_tax", "score", None, "BF11 · entity tax and US"),
    ("bf3_activity", "score", None, "BF3 · activity"),
    ("bf4_money", "score", None, "BF4 · money in and out"),
    ("bf5_role", "score", None, "BF5 · role and authority"),
    ("bf6_bf7_identity", "score", None, "BF6 BF7 · applicant identity"),
    ("bf8_ownership", "score", None, "BF8 · ownership"),
    ("bf9_owner_identity", "score", None, "BF9 · owner identity"),
    ("rescore_after_edits", "score", None, "no edits · total 24"),
    ("bf10_bf12_screening", "score", None, "BF10 BF12 · screening"),
    ("score_total", "score", None, "Score 38.5 · Low"),
]


def _slug(name: str) -> str:
    cleaned: list[str] = []
    for char in name.lower():
        if char.isalnum():
            cleaned.append(char)
        elif not cleaned or cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-")[:48]


def _stories(rows: list[tuple]) -> list[dict]:
    built = []
    for name, blurb, length, end, start in rows:
        path = [start, end] if start != end else [end]
        built.append({"id": _slug(name), "name": name, "blurb": blurb, "path": path, "expected_steps": length, "origin": "chat"})
    return built


INDIVIDUAL_STORIES = _stories([
    ("Opened — nothing to clarify", "Not a US person, the application looks right, nothing flagged. Scores Medium and opens on Tier-1 limits with no person involved.", 44, "account_opened", "chat_request"),
    ("Edits an answer at confirmation", "Taps Edit answers on the application popup and changes address and volume. The edit is logged, checks and the score re-run, and they confirm again.", 49, "account_opened", "chat_request"),
    ("Side business, stays personal", "Registered company, but they keep a personal account. Approved at any volume; the band is the monitoring baseline.", 48, "account_opened", "chat_request"),
    ("US person", "Says yes to US person. Told what works, asked for the TIN (or later), crypto off. Everything else opens as normal.", 46, "account_opened", "chat_request"),
    ("Non-EEA citizen, residence evidence", "Turkish citizen living in Germany. One clarification card, then proof of address or the residence permit checked at Sumsub.", 50, "account_opened", "chat_request"),
    ("Scores High — a person approves", "A declared or screened PEP, or any flagged factor in High. The account shell opens; one person approves before the rest unlocks.", 42, "account_opened", "chat_request"),
    ("Sanctions match", "Confirmed match. The process stops with no reason given.", 41, "cannot_open", "chat_request"),
    ("Opening it for someone else", "Only the person who’ll use the account can open it.", 39, "not_own_behalf", "chat_request"),
    ("ID fails three times at Sumsub", "Retries happen inside the step. The third failure hands it to a person.", 28, "scan_failed_review", "chat_request"),
    ("Switches to business", "A registered company wanting payments in the company name. The answers so far carry over.", 23, "switch_to_business", "chat_request"),
    ("Under 18", "A friendly finish, not an error.", 16, "under_18", "chat_request"),
    ("Lives somewhere Kai isn’t", "Types a country that isn’t on the list. Ends with notify-me.", 13, "residence_not_served", "chat_request"),
    ("Signs up with Google", "Google hands back an email it has already verified, so there is no Kai code and no password.", 41, "account_opened", "chat_request"),
])

BUSINESS_STORIES = _stories([
    ("Opened — owner and director, one co-owner", "Registry found and confirmed, nothing flagged. Lea is matched as a director; Jonas gets his own link.", 65, "account_opened", "chat_request"),
    ("Handed over from personal sign-up", "Started as a personal account, picked “Business account” at step 4b. Sign-up and the basics come with them.", 55, "account_opened", "handoff_from_individual"),
    ("Sole owner and director", "“Just me” — no owner links to send, so the account opens on full Tier-1 limits straight away.", 61, "account_opened", "chat_request"),
    ("Authorised person, not a director", "Adds a board resolution naming them. Doc AI classifies it and matches the names against the registry.", 67, "account_opened", "chat_request"),
    ("“Something else”, classified by AI", "Describes the business in a sentence. AI maps it to E-commerce & retail and the flow carries on.", 66, "account_opened", "chat_request"),
    ("Edits an answer at confirmation", "Changes monthly volume to over €25,000. The edit is logged, BF4 re-scores, and they confirm again.", 70, "account_opened", "chat_request"),
    ("Shares don’t add up", "The owners come to 115%. One correction card, then on — the correction is flagged for a person to glance at.", 65, "account_opened", "chat_request"),
    ("Wrong company — back to the number", "The registry found a different company. Back to the number, found the right one, carried on.", 73, "account_opened", "chat_request"),
    ("Registry details out of date", "The new address is kept beside the registry one; a person reconciles both before the account opens.", 67, "account_opened", "chat_request"),
    ("High-risk sector", "Construction & trades. One non-blocking note; the account shell opens and a person approves.", 64, "account_opened", "chat_request"),
    ("US entity", "Unticks “not a US entity”. Noted, the flow carries on, and a person reviews before the account opens.", 64, "account_opened", "chat_request"),
    ("Will hold client money", "Can’t tick the client-money declaration. A person looks at the business model.", 60, "client_money_review", "chat_request"),
    ("Sanctions match", "A confirmed match on the company or an owner. Stops with no reason given.", 62, "cannot_open", "chat_request"),
    ("Director claim doesn’t match", "Says they’re a director; the registry doesn’t list them. A person checks.", 38, "role_conflict_review", "chat_request"),
    ("ID fails three times", "Retries happen inside the step. The third failure hands it to a person.", 45, "scan_failed_review", "chat_request"),
    ("An owner is a company", "Caught at the owners step. A friendly not-yet with notify-me.", 49, "owner_is_company", "chat_request"),
    ("Registry shows a company shareholder", "Said “owned by people”, but the registry lists a company. The backstop catches it.", 19, "corporate_shareholder", "chat_request"),
    ("Company not active", "The registry says dissolved. The record decides.", 19, "company_not_active", "chat_request"),
    ("Group or holding", "Answered at step 2, before any lookup or scan. A friendly not-yet.", 13, "structure_not_served", "chat_request"),
    ("Company country not served", "Types a country that isn’t on the business list.", 15, "company_country_not_served", "chat_request"),
    ("Signs up with Google", "Google hands back an email it has already verified, so there is no Kai code and no password.", 62, "account_opened", "chat_request"),
])


def _with_questions(steps: list[dict]) -> list[dict]:
    samples = {
        "ask_signup_method": {
            "step": 1,
            "label": "1 · how you sign up",
            "ask": "Let's get you set up. How would you like to sign up?",
            "type": "simple_select",
            "options": [
                {"label": "Email and password", "port": "email_and_password"},
                {"label": "Continue with Google", "port": "continue_with_google"},
            ],
        },
        "ask_email_password": {
            "step": "1a",
            "label": "1a · email and password",
            "type": "free_text",
            "fields": [
                {"id": "email", "ask": "What's your email?", "type": "email"},
                {"id": "password", "ask": "Set a password.", "type": "password"},
            ],
        },
    }
    for step in steps:
        if step.get("kind") not in {"question", "question_popup"}:
            continue
        sample = samples.get(step["id"])
        if sample:
            step["input"] = sample
            step["line"] = sample.get("ask") or step.get("line")
            continue
        step["input"] = {
            "label": step.get("purpose") or step["id"],
            "ask": step.get("line") or step.get("purpose") or "",
            "type": "free_text",
            "fields": [{"id": "answer", "ask": step.get("purpose") or "Your answer", "type": "text"}],
        }
    return steps


def _empty_contract(summary: str, examples: list[str], exclusions: list[str] | None = None) -> dict:
    from kai.engine.slots import sync_try_prompts

    return sync_try_prompts(
        {
            "setup": summary,
            "description": summary,
            "examples": list(examples),
            "exclusions": list(exclusions or []),
            "bands": {"start_at": 0.75, "ask_at": 0.5, "not_this_below": 0.5},
            "slots": [],
            "lines": {},
            "never": [
                {
                    "id": "no_password",
                    "mode": "judged",
                    "text": "Never ask for a password or PIN",
                    "forbidden": ["what's your password", "enter your pin", "share your password"],
                },
                {
                    "id": "no_fake_pass",
                    "mode": "judged",
                    "text": "Never invent a KYC pass",
                    "forbidden": ["you're already approved", "kyc already passed"],
                },
            ],
            "try_prompts": [],
            "preview_ledger": {"accounts": {}},
        }
    )


def bundles() -> list[dict]:
    config = eval_config()
    individual_summary = "Opens a personal account through a conversation with Kai. Each step is a question with its own answer type; the answer decides what comes next. POC — all nine steps of the compliance tree."
    business_summary = "Opens a Kai for Business account through a conversation with Kai. Registry first: the company record is pulled, not typed. One company, owned directly by people."
    individual_steps = _with_questions(layout(INDIVIDUAL))
    business_steps = _with_questions(layout(BUSINESS))
    individual_stories = [dict(item) for item in INDIVIDUAL_STORIES]
    business_stories = [dict(item) for item in BUSINESS_STORIES]
    return [
        {
            "key": "individual-onboarding",
            "name": "Individual onboarding",
            "group": "onboarding",
            "channels": ["chat"],
            "summary": individual_summary,
            "graph": compile_paths(individual_steps, individual_stories, {"chat": "chat_request", "handoff": "chat_request"}),
            "chat_contract": _empty_contract(
                individual_summary,
                ["I want to open a personal account", "open an account for myself", "individual onboarding", "onboarding"],
                ["business account", "company account", "Kai for Business", "business onboarding"],
            ),
            "stories": individual_stories,
            "eval_config": config,
        },
        {
            "key": "business-onboarding",
            "name": "Business onboarding",
            "group": "onboarding",
            "channels": ["chat"],
            "summary": business_summary,
            "graph": compile_paths(business_steps, business_stories, {"chat": "chat_request", "handoff": "handoff_from_individual"}),
            "chat_contract": _empty_contract(
                business_summary,
                ["I want to open a business account", "open a company account", "Kai for Business", "business onboarding"],
                ["personal account", "for myself", "individual onboarding"],
            ),
            "stories": business_stories,
            "eval_config": config,
        },
    ]
