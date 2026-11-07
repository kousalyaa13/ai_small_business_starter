"""Run only the problem-decomposition step and print the components.

Usage:
  python scripts/try_decompose.py                      # built-in sample problems
  python scripts/try_decompose.py "Your problem here"  # one problem
  python scripts/try_decompose.py --budget 30 "..."    # with a monthly budget

Uses OpenAI if OPENAI_API_KEY is set, else OpenRouter if OPENROUTER_API_KEY is set,
otherwise the offline keyword stub.
No web search, database or catalog lookup is involved.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agent.decompose import DECOMPOSE_INSTRUCTIONS, clean_decomposition, intake_prompt  # noqa: E402
from agent.llm import default_model, model_for  # noqa: E402
from agent.schemas import Decomposition, Intake  # noqa: E402

SAMPLES = [
    "Whenever someone fills out our contact form, add them to our CRM without creating duplicate contacts. "
    "Keep additional software costs under $30 per month.",
    "Customers keep missing their appointments at my salon. I want online booking and text reminders the day before.",
    "After each job, I want to send the customer an invoice automatically and record the payment in my books.",
    "We get the same questions about opening hours and delivery on Instagram and our website chat every day.",
    "I'd like to post on Instagram and Facebook three times a week without writing every post myself.",
]


def run(model, problem, budget, software):
    intake = Intake(problem_text=problem, monthly_budget=budget, existing_software=software)
    result, usage = model.structured("decompose", DECOMPOSE_INSTRUCTIONS, intake_prompt(intake, 0), Decomposition)
    components, questions = clean_decomposition(result, intake)
    print("=" * 88)
    print(f"PROBLEM: {problem}")
    print(f"SUMMARY: {result.summary}")
    print("COMPONENTS:")
    for c in components:
        line = f"  {c.id}  [{c.capability}]  {c.description}"
        if c.connects:
            line += f"   (connects {', '.join(c.connects)})"
        print(line)
        for constraint in c.constraints:
            print(f"        - constraint: {constraint}")
    if questions:
        print("CLARIFYING QUESTIONS:")
        for q in questions:
            print(f"  {q.id} ({q.field}): {q.question}")
    print(f"USAGE: {usage.provider}/{usage.model}  in={usage.input_tokens} "
          f"(cached {usage.cached_input_tokens})  out={usage.output_tokens}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("problem", nargs="*", help="problem statement (defaults to built-in samples)")
    parser.add_argument("--budget", type=float, default=None, help="monthly software budget in USD")
    parser.add_argument("--software", default="", help="comma-separated software already used")
    args = parser.parse_args()

    model = default_model()
    if model.name == "stub":
        label = "offline stub (set OPENROUTER_API_KEY or OPENAI_API_KEY for a real model)"
    else:
        label = f"{model.name} ({model_for('decompose', model.name)})"
    print(f"Model: {label}")
    software = [s.strip() for s in args.software.split(",") if s.strip()]
    for problem in ([" ".join(args.problem)] if args.problem else SAMPLES):
        run(model, problem, args.budget, software)


if __name__ == "__main__":
    main()
