import jax
import jax.numpy as jnp
import numpy as np
from jumanji.environments.routing.sokoban.constants import AGENT, BOX, EMPTY, TARGET, TARGET_AGENT, TARGET_BOX, WALL
from jumanji.environments.routing.sokoban.env import Sokoban
from jumanji.environments.routing.sokoban.generator import HuggingFaceDeepMindGenerator

from actions import action_id, normalize_action

SYMBOLS = {
    EMPTY: " ",
    WALL: "#",
    TARGET: ".",
    AGENT: "@",
    BOX: "$",
    TARGET_AGENT: "+",
    TARGET_BOX: "*",
}


class SokobanSession:
    def __init__(self, seed: int = 0, time_limit: int = 120) -> None:
        generator = HuggingFaceDeepMindGenerator(dataset_name="unfiltered-test")
        self.env = Sokoban(generator=generator, time_limit=time_limit)
        self.state, self.timestep = self.env.reset(jax.random.PRNGKey(seed))
        self.states = [self.state]
        self.actions: list[str] = []
        self.rewards: list[float] = []

    @property
    def done(self) -> bool:
        return bool(np.asarray(self.timestep.last()).item())

    @property
    def solved(self) -> bool:
        return bool(np.asarray(self.timestep.extras.get("solved", False)).item())

    @property
    def step_count(self) -> int:
        return int(np.asarray(self.state.step_count).item())

    @property
    def total_reward(self) -> float:
        return float(sum(self.rewards))

    def ascii_board(self) -> str:
        grid = np.asarray(self.env.grid_combine(self.state.variable_grid, self.state.fixed_grid))
        return "\n".join("".join(SYMBOLS[int(cell)] for cell in row) for row in grid)

    def step(self, action: str | int) -> bool:
        name = normalize_action(action)
        before = np.asarray(self.state.variable_grid)
        self.state, self.timestep = self.env.step(self.state, jnp.asarray(action_id(name), dtype=jnp.int32))
        self.states.append(self.state)
        self.actions.append(name)
        self.rewards.append(float(np.asarray(self.timestep.reward).item()))
        return not np.array_equal(before, np.asarray(self.state.variable_grid))
