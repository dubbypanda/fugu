import argparse
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from actions import parse_action_sequence
from env import SokobanSession
from renderer import save_mp4

PROMPT = """Solve this Sokoban board.
Return only JSON like {{"actions":["up","right","down","left"]}}.
Use only: up, right, down, left.

Legend: # wall, space floor, . target, $ box, @ player, * box on target, + player on target.
Step {step}/{max_steps}. Previous actions: {actions}

{board}
"""


def ask_model(client: OpenAI, model: str, session: SokobanSession, plan_actions: int, max_steps: int) -> list[str]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You solve Sokoban. Reply with JSON actions only."},
            {
                "role": "user",
                "content": PROMPT.format(
                    board=session.ascii_board(),
                    step=session.step_count,
                    max_steps=max_steps,
                    actions=session.actions[-20:] or "none",
                ),
            },
        ],
        temperature=0,
        extra_body={"reasoning": {"effort": "xhigh"}},
    )
    return parse_action_sequence(response.choices[0].message.content or "")[:plan_actions]


def output_path(output: str | None, model: str, seed: int) -> Path:
    path = Path(output or f"seed-{seed}.mp4")
    if path.is_absolute() or path.parent != Path("."):
        return path
    model_dir = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return Path("results") / model_dir / path


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Ask MODEL to solve Sokoban and render the attempt.")
    parser.add_argument("--output", help="Optional MP4 filename or path. Defaults to seed-<seed>.mp4.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=120)
    parser.add_argument("--plan-actions", type=int, default=30)
    args = parser.parse_args()

    model = os.environ["MODEL"]
    output = output_path(args.output, model, args.seed)
    client = OpenAI(api_key=os.getenv("API_KEY"), base_url=os.getenv("BASE_URL") or None)
    session = SokobanSession(seed=args.seed, time_limit=args.max_steps)

    while not session.done and session.step_count < args.max_steps:
        print(f"\nstep {session.step_count}\n{session.ascii_board()}")
        actions = ask_model(client, model, session, args.plan_actions, args.max_steps)
        if not actions:
            print("model returned no actions; stopping")
            break
        print("actions:", ", ".join(actions))

        for action in actions:
            changed = session.step(action)
            if session.done or session.step_count >= args.max_steps or not changed:
                break

    save_mp4(session.env, session.states, output)
    print(
        f"\nsolved={session.solved} steps={session.step_count} "
        f"reward={session.total_reward:.2f} video={output}"
    )


if __name__ == "__main__":
    main()
