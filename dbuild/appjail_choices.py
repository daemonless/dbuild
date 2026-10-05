"""Each choice option's AppJail form, for fjord.

The catalog's AppJail bundle is the default answer to every choice. An option
that adds services (immich's public proxy, a MariaDB for Vikunja) changes the
compose, which podman runs, but AppJail runs appjail-director.yml, which never
saw the option -- so fjord refused every non-default pick on AppJail.

Here an option's AppJail form is what its director has beyond the default
one: the services and volumes it adds, rendered by the same code that renders
the bundle (the authored director sliced by option, or the director template
with the option's database jail), plus any file the new jails reference.
fjord adds them to the bundle's director and drops what the option drops.
"""

from __future__ import annotations

import copy
from typing import Any

from dbuild import docs


def _director_dict(text: str) -> dict[str, Any]:
    """Parse director YAML, keeping each !ENV value as the text that wrote it
    so the fragment re-renders exactly as the bundle does."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    # A plain string, the way an author writes it in x-daemonless:
    # docs._render_director_override turns it back into the tag.
    def env(loader: yaml.SafeLoader, node: yaml.Node) -> str:
        return f"!ENV '{loader.construct_scalar(node)}'"

    Loader.add_constructor("!ENV", env)
    return yaml.load(text, Loader=Loader) or {}


def _added(full: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The services and volumes full has that base does not."""
    out: dict[str, Any] = {}
    for key in ("services", "volumes"):
        have = base.get(key) or {}
        more = {k: v for k, v in (full.get(key) or {}).items() if k not in have}
        if more:
            out[key] = more
    return out


def _render_template_director(cfg: Any, appjail: dict[str, Any]) -> str:
    """The generated (non-authored) director for cfg with appjail metadata
    replaced: the bundle's own template, in its deploy render."""
    env = docs._get_jinja_env(docs.Path.cwd())
    if env is None:
        raise RuntimeError("Could not find dbuild templates")
    context = docs._enrich_metadata(cfg)
    context["render_mode"] = "deploy"
    context["appjail"] = appjail
    return env.get_template("appjail-director.yml.j2").render(context)


def option_forms(cfg: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """choice id -> option id -> {"director": text, "files": {name: text},
    "hostnames": {director service: variable}}
    for every option that adds jails. An option that only drops services or
    sets values has none: fjord handles those from drop and env."""
    aj = cfg.metadata.appjail if isinstance(cfg.metadata.appjail, dict) else {}
    authored = aj.get("director") if isinstance(aj.get("director"), dict) and aj["director"].get("services") else None
    out: dict[str, dict[str, dict[str, Any]]] = {}

    if authored is None:
        base_text = _render_template_director(cfg, aj)
        base = _director_dict(base_text)

    for c in cfg.metadata.choices:
        for o in c.options:
            files: dict[str, str] = {}
            hostnames: dict[str, str] = {}
            if authored is not None:
                if not o.profile:
                    continue
                added = _added(docs._director_for_option(authored, cfg, [o]),
                               docs._director_for_option(authored, cfg, None))
            else:
                if not o.director_dep:
                    continue
                with_dep = copy.deepcopy(aj)
                with_dep["depends_on"] = [*(with_dep.get("depends_on") or []), o.director_dep]
                added = _added(_director_dict(_render_template_director(cfg, with_dep)), base)
                if o.jail_template_file and o.jail_template:
                    files[o.jail_template_file] = o.jail_template
                # The jail's own service name in the director: what fjord
                # names the jail by, and so what the app's host variable
                # must say there (the compose calls it "mariadb", the
                # director "vikunja-mariadb").
                for var in o.hostnames.values():
                    hostnames[o.director_dep["name"]] = var
            if not added:
                continue
            text = docs._render_director_override(added)
            text = text.replace("# appjail-director.yml\n\n", "", 1)
            out.setdefault(c.id, {})[o.id] = {"director": text, "files": files, "hostnames": hostnames}
    return out


def merge_into(data: dict[str, Any], forms: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Put each option's AppJail form on its option in choices.to_fjord's
    output, as `appjail`."""
    for c in data.get("choices") or []:
        for o in c.get("options") or []:
            form = (forms.get(c["id"]) or {}).get(o["id"])
            if form:
                o["appjail"] = form
    return data

