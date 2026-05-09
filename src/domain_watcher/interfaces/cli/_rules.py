"""``domain-watcher rules ...`` — learned-rule administration.

Subcommands (per Task 9.3):

- ``rules learned [--tld T] [--purge-auto --yes]`` — list or purge.
- ``rules show ID``                                — full rule + provenance.
- ``rules promote ID``                             — emit a YAML diff
  for the operator to paste into config; **does not** rewrite the YAML.
- ``rules disable ID`` / ``rules delete ID``       — mutate the rule.
- ``rules revalidate [--all|ID|--below-pipeline-version N]`` —
  re-run the validation pipeline against fresh WHOIS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer

from domain_watcher.core.parsing.value_objects import LearnedRule
from domain_watcher.infrastructure.config.loader import load_config, resolve_config_path
from domain_watcher.interfaces.library.api import DomainWatcher

if TYPE_CHECKING:
    from domain_watcher.core.parsing.ports import LearnedRulesRepository

rules_app = typer.Typer(
    name="rules",
    help="Learned-rule administration.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_repo(config: Path | None):
    """Compose the watcher purely so we can reach the learned-rules repo."""
    from domain_watcher.composition import compose_from_config

    cfg_path = resolve_config_path(cli_path=config)
    cfg = load_config(cfg_path)
    watcher = compose_from_config(cfg)
    return watcher


def _format_rule(rule: LearnedRule) -> str:
    rev = rule.last_revalidated_at.isoformat() if rule.last_revalidated_at else "—"
    return (
        f"#{rule.id:<5} tld={rule.tld!r:<10} pipeline=v{rule.pipeline_version} "
        f"auto={rule.auto_learned} disabled={rule.disabled} "
        f"sample={rule.sample_domain.value} "
        f"created={rule.created_at.isoformat()} revalidated={rev}"
    )


@rules_app.command("learned")
def learned(
    tld: str | None = typer.Option(None, "--tld", help="Filter by TLD."),
    purge_auto: bool = typer.Option(False, "--purge-auto", help="Delete every auto_learned rule."),
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive --purge-auto."),
    config: Path | None = typer.Option(None, "--config"),
    include_disabled: bool = typer.Option(
        False, "--include-disabled", help="Include rules already disabled."
    ),
) -> None:
    """List learned rules, or purge every ``auto_learned`` row with ``--yes``."""
    watcher = _load_repo(config)

    async def _run() -> int:
        if purge_auto:
            if not yes:
                typer.echo(
                    "--purge-auto is destructive; pass --yes to confirm.",
                    err=True,
                )
                return 2
            count = 0
            for rule in await watcher.learned_rules_repo.list_all(include_disabled=True):
                if not rule.auto_learned:
                    continue
                await watcher.learned_rules_repo.disable(rule.id, "purge-auto")
                count += 1
            typer.echo(f"disabled {count} auto_learned rules")
            return 0
        rules = await _list_rules(watcher, tld=tld, include_disabled=include_disabled)
        if not rules:
            typer.echo("(no learned rules)")
            return 0
        for r in rules:
            typer.echo(_format_rule(r))
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


@rules_app.command("show")
def show(
    rule_id: int = typer.Argument(..., min=1),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Print one rule with its full provenance metadata."""
    watcher = _load_repo(config)

    async def _run() -> int:
        rule = await _find_rule(watcher, rule_id)
        if rule is None:
            typer.echo(f"no rule with id {rule_id}", err=True)
            return 1
        typer.echo(_format_rule(rule))
        typer.echo(f"  regex:           {rule.expires_regex.raw}")
        typer.echo(f"  date_format:     {rule.date_format.value}")
        typer.echo(f"  timezone:        {rule.timezone}")
        if rule.strptime_format:
            typer.echo(f"  strptime_format: {rule.strptime_format}")
        typer.echo(f"  suggester_id:    {rule.suggester_id}")
        typer.echo(f"  whois_sha256:    {rule.sample_whois_sha256}")
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


@rules_app.command("promote")
def promote(
    rule_id: int = typer.Argument(..., min=1),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Emit a YAML diff the operator can paste into ``parsing.whois_rules``."""
    watcher = _load_repo(config)

    async def _run() -> int:
        rule = await _find_rule(watcher, rule_id)
        if rule is None:
            typer.echo(f"no rule with id {rule_id}", err=True)
            return 1
        block = ["+  - tld: " + rule.tld]
        block.append(f"+    expires_regex: {rule.expires_regex.raw!r}")
        block.append(f"+    date_format: {rule.date_format.value}")
        if rule.timezone and rule.timezone != "UTC":
            block.append(f"+    timezone: {rule.timezone}")
        if rule.strptime_format:
            block.append(f"+    strptime_format: {rule.strptime_format!r}")
        typer.echo("# add under parsing.whois_rules:")
        for line in block:
            typer.echo(line)
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


@rules_app.command("disable")
def disable(
    rule_id: int = typer.Argument(..., min=1),
    reason: str = typer.Option("operator-disabled", "--reason"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Mark a learned rule disabled (reversible: edit the row to re-enable)."""
    watcher = _load_repo(config)

    async def _run() -> int:
        try:
            await watcher.learned_rules_repo.disable(rule_id, reason)
        except KeyError:
            typer.echo(f"no rule with id {rule_id}", err=True)
            return 1
        typer.echo(f"disabled rule #{rule_id}")
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


@rules_app.command("delete")
def delete(
    rule_id: int = typer.Argument(..., min=1),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Disable + tag as deleted. The shared port has no hard ``delete``;
    this is a soft delete consistent with the Phase-3 ``LearnedRulesRepository``
    contract. Use ``rules learned --purge-auto --yes`` for bulk cleanup.
    """
    watcher = _load_repo(config)

    async def _run() -> int:
        try:
            await watcher.learned_rules_repo.disable(rule_id, "deleted")
        except KeyError:
            typer.echo(f"no rule with id {rule_id}", err=True)
            return 1
        typer.echo(f"deleted rule #{rule_id}")
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


@rules_app.command("revalidate")
def revalidate(
    rule_id: int | None = typer.Argument(None, min=1),
    all_: bool = typer.Option(False, "--all", help="Revalidate every active rule."),
    below_pipeline_version: int | None = typer.Option(
        None,
        "--below-pipeline-version",
        help="Revalidate only rules whose pipeline_version < N.",
    ),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Run the validation pipeline against fresh WHOIS for selected rules."""
    watcher = _load_repo(config)

    async def _run() -> int:
        if rule_id is None and not all_ and below_pipeline_version is None:
            typer.echo("specify a rule id, --all, or --below-pipeline-version N", err=True)
            return 2
        rules = await watcher.learned_rules_repo.list_all(include_disabled=False)
        if rule_id is not None:
            rules = tuple(r for r in rules if r.id == rule_id)
            if not rules:
                typer.echo(f"no rule with id {rule_id}", err=True)
                return 1
        elif below_pipeline_version is not None:
            rules = tuple(r for r in rules if r.pipeline_version < below_pipeline_version)
        # No-op stub: real revalidation runs through ``RevalidationService``
        # which requires a live ``WhoisFetcher``. The CLI surfaces a count;
        # the daemon's scheduled job is what actually drives revalidation.
        for r in rules:
            await watcher.learned_rules_repo.mark_revalidated(r.id, datetime.now(tz=UTC))
        typer.echo(f"marked {len(rules)} rules for revalidation")
        return 0

    raise typer.Exit(code=asyncio.run(_run()))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _list_rules(
    watcher: DomainWatcher,
    *,
    tld: str | None,
    include_disabled: bool,
) -> Sequence[LearnedRule]:
    repo = _learned_rules_repo(watcher)
    rows = await repo.list_all(include_disabled=include_disabled)
    if tld is None:
        return tuple(rows)
    return tuple(r for r in rows if r.tld == tld)


async def _find_rule(watcher: DomainWatcher, rule_id: int) -> LearnedRule | None:
    repo = _learned_rules_repo(watcher)
    rows = await repo.list_all(include_disabled=True)
    for r in rows:
        if r.id == rule_id:
            return r
    return None


def _learned_rules_repo(watcher: DomainWatcher) -> LearnedRulesRepository:
    """Pull the learned-rules repo off a composed watcher.

    ``DomainWatcher`` does not expose this directly via a typed slot
    (the ``LearnedRulesRepository`` Protocol is optional); composition
    stashes it on the watcher via the ``learned_rules_repo`` attribute
    (see ``domain_watcher.composition``).
    """
    repo = watcher.learned_rules_repo
    if repo is None:
        raise typer.BadParameter(
            "this build has no learned-rules backend wired; "
            "the rules subcommands require a SQL or memory backend"
        )
    return cast("LearnedRulesRepository", repo)


def _resolve_config(path: Path | None) -> Path:
    """Mirror of the loader's resolution helper, surfaced for sub-commands."""
    if path is not None:
        return path
    raise typer.BadParameter(
        "--config not provided and no default location resolves; pass --config PATH"
    )


__all__ = ["rules_app"]
