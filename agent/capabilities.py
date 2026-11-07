"""Controlled vocabulary of capabilities that a business problem can be broken into.

The decomposition model must pick from this list so that matching against the
catalog stays deterministic application code rather than model judgement.
"""
from typing import Literal

CAPABILITIES = {
    "form_capture": "Collect submissions from a website or contact form",
    "crm_contacts": "Store and manage customer or lead contact records",
    "email_marketing": "Send newsletters, campaigns or automated marketing email",
    "appointment_booking": "Let customers book appointments and send reminders",
    "invoicing": "Create invoices, accept payments and track bookkeeping",
    "support_chat": "Answer customer questions through chat or a help inbox",
    "social_scheduling": "Plan and publish social media posts",
    "workflow_automation": "Move data between apps when something happens",
    "website_builder": "Build and host a business website",
    "online_store": "Sell products online",
    "ai_content": "Generate marketing copy, images or other content with AI",
    "other": "A need that does not fit any category above",
}

Capability = Literal[
    "form_capture", "crm_contacts", "email_marketing", "appointment_booking",
    "invoicing", "support_chat", "social_scheduling", "workflow_automation",
    "website_builder", "online_store", "ai_content", "other",
]

# Capabilities whose plan limits usually depend on contact or event volume.
VOLUME_SENSITIVE = {"crm_contacts", "email_marketing", "workflow_automation", "form_capture"}

# Keyword hints used by the offline stub model. Real decomposition uses an LLM.
KEYWORDS = {
    "form_capture": ["form", "sign up", "signup", "submission", "intake"],
    "crm_contacts": ["crm", "contact", "lead", "customer list", "customer record", "client list"],
    "email_marketing": ["newsletter", "email campaign", "mailing list", "promotion", "win back", "come back", "repeat customer", "retain", "loyal"],
    "appointment_booking": ["appointment", "booking", "book ", "schedule", "calendar", "no-show", "reminder"],
    "invoicing": ["invoice", "bookkeep", "expense", "payment", "accounting", "cash flow"],
    "support_chat": ["question", "support", "chat", "faq", "customer service", "inbox"],
    "social_scheduling": ["social", "instagram", "facebook", "post", "tiktok"],
    "website_builder": ["website", "web site", "landing page", "online presence", "seo"],
    "online_store": ["online store", "sell online", "ecommerce", "e-commerce", "shop"],
    "ai_content": ["generate", "copy", "content", "design", "graphics", "write"],
}
