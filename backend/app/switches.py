"""Polling switches at four levels. An endpoint is polled only when every level is on:

  1. fleet          the whole platform (poll_switch 'fleet')
  2. endpoint type  e.g. MMF for every plant (poll_switch 'type:mmf')
  3. plant          plant.polling_enabled (and the plant's status must be 'live')
  4. endpoint       plant_endpoint.enabled

Switches only stop scheduled polling. Manual "Test" and "Poll now" still run, so an admin can check
a paused endpoint before switching it back on.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CATEGORIES, Plant, PlantEndpoint, PollSwitch

TYPE_LABEL = {"rolling": "Rolling", "dayahead": "Day-ahead", "weekahead": "Week-ahead",
              "fourmonthsahead": "4-months-ahead", "mmf": "MMF"}


@dataclass
class Switches:
    fleet: bool = True
    types: dict[str, bool] = field(default_factory=lambda: {c: True for c in CATEGORIES})


async def load_switches(s: AsyncSession) -> Switches:
    sw = Switches()
    for row in (await s.execute(select(PollSwitch))).scalars().all():
        if row.key == "fleet":
            sw.fleet = row.enabled
        elif row.key.startswith("type:") and row.key[5:] in sw.types:
            sw.types[row.key[5:]] = row.enabled
    return sw


def off_reason(sw: Switches, plant: Plant, ep: PlantEndpoint) -> str | None:
    """Why this endpoint is not polled on schedule, or None when it is."""
    if not sw.fleet:
        return "Fleet retrieval is paused"
    if not sw.types.get(ep.category, True):
        return f"{TYPE_LABEL.get(ep.category, ep.category)} is paused for all plants"
    if plant.status != "live":
        return f"Plant status is {plant.status}"
    if not plant.polling_enabled:
        return "Retrieval is paused for this plant"
    if not ep.enabled:
        return "This endpoint is paused"
    return None
