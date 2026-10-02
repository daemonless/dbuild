"""Stack choices: what a compose offers under ``x-daemonless.choices``.

Two kinds of choice. The common one is a **database**: the app says which
engines it can run on and what it calls the connection in its own env, and
dbuild supplies the services -- one definition of a daemonless postgres or
mariadb service, shared by every app, fixed in one place::

    x-daemonless:
      choices:
        database:
          kind: database
          offers: [sqlite, postgres, mariadb, external]
          default: sqlite
          env:
            type: VIKUNJA_DATABASE_TYPE       # the app's own names
            host: VIKUNJA_DATABASE_HOST
            user: VIKUNJA_DATABASE_USER
            password: VIKUNJA_DATABASE_PASSWORD
            name: VIKUNJA_DATABASE_DATABASE
          types: { mariadb: mysql }           # the app's word for an engine, when it differs

The app's service reads those variables (``${VIKUNJA_DATABASE_TYPE:-sqlite}``
and so on); lint checks they are there. The generated database service reads
the same variables, so one .env feeds both and nothing is wired by hand.
``external`` is a database the person already runs: no service, the
connection asked for.

The other kind is a **part** the app can run with or without (immich's
machine learning). That one is the app's own, so it is written out: a
profile to switch on, env to set, values to ask for::

    choices:
      machine_learning:
        label: Machine learning
        default: "on"
        options:
          "on":  { label: On, profile: ml }
          "off": { label: Off, env: { IMMICH_ML_ENABLED: "false" } }

Either way the default option is the compose as it stands: it names no
profile and adds no service, so ``podman-compose up -d`` with nothing set
is the default answer -- what a script, or a host with no UI, gets.

For everyone who copies files instead of running fjord, each non-default
option gets a flattened pair: ``compose.<choice>-<option>.yaml`` with that
option's services in and the others out, and ``example.<choice>-<option>.env``
with its values applied. Generated, committed like README.md, never edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Lazy: the wheel build imports the package without yaml installed.
try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# The database engines dbuild knows how to run. One place: an app that
# offers postgres gets this service, and a fix here fixes every app.
ENGINES: dict[str, dict[str, Any]] = {
    "sqlite": {"label": "SQLite", "type": "sqlite",
               "doc": "A file in the app's config folder. Right for one person, nothing extra to run."},
    "postgres": {
        "label": "PostgreSQL", "type": "postgres", "service": "postgres", "port": 5432,
        "image": "ghcr.io/daemonless/postgres:17", "data": "/var/lib/postgresql/data",
        "env": {"user": "POSTGRES_USER", "password": "POSTGRES_PASSWORD", "name": "POSTGRES_DB"},
        # PostgreSQL wants SysV shared memory, which a jail does not get by
        # default; without it the server fails with an error that reads like
        # a configuration problem.
        "annotations": {"org.freebsd.jail.allow.sysvipc": "true"},
        "jail_template": (
            "# The jail PostgreSQL runs in: SysV shared memory, which a jail does not\n"
            "# get by default. ip4/ip6 are set here because the director's ip4_inherit\n"
            "# option is a no-op in AppJail 5.5.0.\n\n"
            "exec.start: \"/bin/sh /etc/rc\"\nexec.stop: \"/bin/sh /etc/rc.shutdown jail\"\n"
            "sysvmsg: new\nsysvsem: new\nsysvshm: new\nmount.devfs\npersist\nip4: inherit\nip6: inherit\n"
        ),
        "doc": "One more container, its data in its own folder. For a household, or an app that wants it.",
    },
    "mariadb": {
        "label": "MariaDB", "type": "mysql", "service": "mariadb", "port": 3306,
        "image": "ghcr.io/daemonless/mariadb:11.4", "data": "/config",
        "env": {"user": "MYSQL_USER", "password": "MYSQL_PASSWORD", "name": "MYSQL_DATABASE"},
        "extra_env": {"MYSQL_ROOT_PASSWORD": "${{{password}}}"},
        "doc": "One more container, its data in its own folder. If you already know MariaDB, or the app prefers it.",
    },
    "external": {"label": "Your own", "type": "",
                 "doc": "A database you already run, here or on another machine. Nothing extra runs; you give the address and the account."},
}


@dataclass
class Ask:
    name: str
    label: str = ""
    default: str = ""
    type: str = "string"            # string | secret
    values: dict[str, str] = field(default_factory=dict)  # a fixed set to pick from


@dataclass
class Option:
    id: str
    label: str
    doc: str = ""
    profile: str = ""
    env: dict[str, str] = field(default_factory=dict)
    ask: list[Ask] = field(default_factory=list)
    # Services this option leaves out (a part the default runs and this
    # option does without: immich without machine learning).
    drop: list[str] = field(default_factory=list)
    # A service dbuild adds for this option (database kind), as compose YAML
    # text under services:, and the .env lines it needs beyond env/ask.
    service_yaml: str = ""
    env_lines: list[str] = field(default_factory=list)
    # The same service for appjail-director: a depends_on entry the director
    # template renders, the env that differs there (the host is the jail's
    # name), and a jail template file when the engine needs one.
    director_dep: dict[str, Any] = field(default_factory=dict)
    env_appjail: dict[str, str] = field(default_factory=dict)
    jail_template_file: str = ""
    jail_template: str = ""


@dataclass
class Choice:
    id: str
    label: str
    kind: str = "part"               # part | database
    doc: str = ""
    default: str = ""
    service: str = ""                # the app service the choice wires (database); "" = first
    env_map: dict[str, str] = field(default_factory=dict)   # database: role -> the app's variable
    options: list[Option] = field(default_factory=list)

    @property
    def default_option(self) -> Option | None:
        return next((o for o in self.options if o.id == self.default), None)


def _database_options(cid: str, c: dict[str, Any], services: dict[str, Any]) -> tuple[list[Option], dict[str, str]]:
    env_map = {str(k): str(v) for k, v in (c.get("env") or {}).items()}
    types = {str(k): str(v) for k, v in (c.get("types") or {}).items()}
    offers = [str(x) for x in (c.get("offers") or [])]
    app = str(c.get("service", "")) or (next(iter(services)) if services else "app")
    var = lambda role, fallback: env_map.get(role, fallback)  # noqa: E731
    v_type, v_host, v_port = var("type", ""), var("host", ""), var("port", "")
    v_user, v_pass, v_name = var("user", f"{cid.upper()}_USER"), var("password", f"{cid.upper()}_PASSWORD"), var("name", f"{cid.upper()}_NAME")
    v_data = f"{cid.upper()}_LOCATION"   # the fleet's name for a data folder (UPLOAD_LOCATION, DB_DATA_LOCATION)
    opts: list[Option] = []
    for eid in offers:
        e = ENGINES.get(eid)
        if e is None:
            continue
        word = types.get(eid, e["type"])
        if eid == "external":
            kinds = {types.get(k, ENGINES[k]["type"]): ENGINES[k]["label"] for k in offers if k in ("postgres", "mariadb")}
            ask = []
            if v_type:
                ask.append(Ask(name=v_type, label="Kind", values=kinds))
            ask += [Ask(name=v_host, label="Host")] if v_host else []
            ask += [Ask(name=v_port, label="Port")] if v_port else []
            ask += [Ask(name=v_user, label="User"), Ask(name=v_pass, label="Password", type="secret"), Ask(name=v_name, label="Database")]
            opts.append(Option(id=eid, label=e["label"], doc=e["doc"], ask=ask))
            continue
        env = {v_type: word} if v_type else {}
        if "service" not in e:            # sqlite: nothing to run
            opts.append(Option(id=eid, label=e["label"], doc=e["doc"], env=env))
            continue
        # A service dbuild supplies. It reads the app's variables, so one
        # .env feeds both sides.
        env.update({v_host: e["service"]} if v_host else {})
        env.update({v_port: str(e["port"])} if v_port else {})
        extra = "".join(f"      - {k}={v.format(password=v_pass)}\n" for k, v in (e.get("extra_env") or {}).items())
        ann = "".join(f"      {k}: \"{v}\"\n" for k, v in (e.get("annotations") or {}).items())
        service_yaml = (
            f"  {e['service']}:\n"
            f"    image: {e['image']}\n"
            f"    restart: always\n"
            + (f"    annotations:\n{ann}" if ann else "")
            + f"    environment:\n"
            f"      - {e['env']['user']}=${{{v_user}}}\n"
            f"      - {e['env']['password']}=${{{v_pass}}}\n"
            f"      - {e['env']['name']}=${{{v_name}}}\n"
            f"{extra}"
            f"    volumes:\n"
            f"      - \"${{{v_data}}}:{e['data']}\"\n"
        )
        env_lines = [f"{v_user}={app}", f"{v_pass}=  # set one", f"{v_name}={app}", f"{v_data}=/containers/{app}/{e['service']}"]
        # appjail-director: the service is a jail named <app>_<engine>, which
        # is also its hostname on the project's network; it starts before the
        # app (director starts by ascending priority, default 99).
        jail = f"{app}-{e['service']}"
        tpl_file = f"{e['service']}-template.conf" if e.get("jail_template") else ""
        dep_env = {e["env"]["user"]: f"!ENV '${{{v_user}}}'", e["env"]["password"]: f"!ENV '${{{v_pass}}}'",
                   e["env"]["name"]: f"!ENV '${{{v_name}}}'"}
        for k, v in (e.get("extra_env") or {}).items():
            dep_env[k] = "!ENV '" + v.format(password=v_pass) + "'"
        director_dep = {"name": jail, "image": e["image"], "template": tpl_file, "priority": 10,
                        "env": dep_env, "volumes": {cid: e["data"]}}
        env_appjail = {v_host: jail.replace("-", "_")} if v_host else {}
        opts.append(Option(id=eid, label=e["label"], doc=e["doc"], env=env, service_yaml=service_yaml, env_lines=env_lines,
                           director_dep=director_dep, env_appjail=env_appjail,
                           jail_template_file=tpl_file, jail_template=e.get("jail_template", "")))
    return opts, env_map


def parse(meta: dict[str, Any], compose_data: dict[str, Any] | None = None) -> list[Choice]:
    """Read ``x-daemonless.choices`` into Choice objects. Shape errors are
    left to lint; here anything unreadable is skipped."""
    raw = meta.get("choices")
    if not isinstance(raw, dict):
        return []
    services = (compose_data or {}).get("services") or {}
    out: list[Choice] = []
    for cid, c in raw.items():
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind", "part"))
        env_map: dict[str, str] = {}
        if kind == "database":
            opts, env_map = _database_options(str(cid), c, services)
            label = str(c.get("label", "Database"))
            doc = str(c.get("doc", "")) or "Where the app keeps its data. The default needs nothing else running."

        else:
            label, doc = str(c.get("label", str(cid))), str(c.get("doc", ""))
            opts = []
            for oid, o in (c.get("options") or {}).items():
                o = o if isinstance(o, dict) else {}
                asks = []
                for a in o.get("ask") or []:
                    if isinstance(a, dict) and a.get("name"):
                        asks.append(Ask(
                            name=str(a["name"]), label=str(a.get("label", "")),
                            default=str(a.get("default", "")), type=str(a.get("type", "string")),
                            values={str(k): str(v) for k, v in (a.get("values") or {}).items()},
                        ))
                opts.append(Option(
                    id=str(oid), label=str(o.get("label", str(oid))), doc=str(o.get("doc", "")),
                    profile=str(o.get("profile", "")),
                    env={str(k): str(v) for k, v in (o.get("env") or {}).items()},
                    ask=asks,
                    drop=[str(x) for x in (o.get("drop") or [])],
                ))
        default = str(c.get("default", "")) or (opts[0].id if opts else "")
        out.append(Choice(id=str(cid), label=label, kind=kind, doc=doc, default=default,
                          service=str(c.get("service", "")), env_map=env_map, options=opts))
    return out


def validate(choices: list[Choice], compose_data: dict[str, Any]) -> list[str]:
    """What lint reports about a declaration."""
    errors: list[str] = []
    services = compose_data.get("services") or {}
    profiled: dict[str, list[str]] = {}
    for name, svc in services.items():
        for p in (svc or {}).get("profiles") or []:
            profiled.setdefault(str(p), []).append(name)
    raw = (compose_data.get("x-daemonless") or {}).get("choices") or {}
    for c in choices:
        where = f"x-daemonless.choices.{c.id}"
        if not c.options:
            errors.append(f"{where}: no options")
            continue
        if c.default_option is None:
            errors.append(f"{where}: default '{c.default}' is not one of its options")
        elif c.default_option.profile or c.default_option.service_yaml:
            errors.append(f"{where}: the default option '{c.default}' switches a service on; "
                          "the default is the compose as it stands")
        if c.kind == "database":
            for eid in (raw.get(c.id) or {}).get("offers") or []:
                if str(eid) not in ENGINES:
                    errors.append(f"{where}: offers '{eid}', which dbuild does not know (one of {', '.join(ENGINES)})")
            app = c.service or (next(iter(services)) if services else "")
            svc = services.get(app) or {}
            raw_env = svc.get("environment") or []
            names = set(raw_env.keys()) if isinstance(raw_env, dict) else {str(e).split("=", 1)[0] for e in raw_env}
            for role, var in c.env_map.items():
                if var not in names:
                    errors.append(f"{where}.env.{role}: {var} is not in {app}'s environment, so the choice would set a variable the app never reads")
            if services.get(c.default_option.id if c.default_option else ""):
                pass
            for o in c.options:
                if o.service_yaml and ENGINES[o.id]["service"] in services:
                    errors.append(f"{where}: the compose already has a '{ENGINES[o.id]['service']}' service; dbuild supplies it for the {o.label} option")
        for o in c.options:
            if o.profile and o.profile not in profiled:
                errors.append(f"{where}.options.{o.id}: no service carries profiles: [{o.profile}]")
            for d in o.drop:
                if d not in services:
                    errors.append(f"{where}.options.{o.id}: drops '{d}', which is not a service")
            if c.default_option is o and o.drop:
                errors.append(f"{where}: the default option '{o.id}' drops a service; the default is the compose as it stands")
            for a in o.ask:
                if a.type not in ("string", "secret"):
                    errors.append(f"{where}.options.{o.id}: ask {a.name}: type must be string or secret")
    return errors


# --- flattening ---------------------------------------------------------

_SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(#.*)?$")


def _as_list(option) -> list[Option]:
    if option is None:
        return []
    return list(option) if isinstance(option, (list, tuple)) else [option]


def flatten_compose(text: str, compose_data: dict[str, Any], option) -> str:
    """The compose for one option, or one option per choice taken together:
    their profiles' services in, every other profiled service out, dropped
    services gone, ``profiles:`` keys gone, and the service dbuild supplies
    (a database) appended under ``services:``.

    Line-based so the comments survive -- they are the explanation a reader
    needs, and a YAML round trip drops them. Checked afterwards by parsing:
    the result has to hold exactly the services the options get.
    """
    options = _as_list(option)
    profiles = {o.profile for o in options if o.profile}
    drop = {d for o in options for d in o.drop}
    option = next((o for o in options if o.service_yaml), None)
    services = compose_data.get("services") or {}
    keep = {n for n, s in services.items()
            if n not in drop
            and (not (s or {}).get("profiles") or profiles & {str(p) for p in (s or {}).get("profiles")})}
    gone = set(services) - keep
    # The app waits for the service dbuild adds: compose starts a
    # depends_on first, so the app does not spend its first seconds
    # retrying a database that is still coming up.
    app = next(iter(services), "")
    dep = _SERVICE_RE.match(option.service_yaml.splitlines()[0]).group(1) if option and option.service_yaml else ""
    out: list[str] = []
    in_services = False
    dropping = False
    skipping_profiles = False
    in_depends = False
    inserted = False
    for line in text.splitlines():
        top = bool(line) and not line[0].isspace() and not line.startswith("#")
        if top:
            if in_services and option and option.service_yaml and not inserted:
                out.append(option.service_yaml.rstrip("\n"))
                inserted = True
            in_services = line.startswith("services:")
            dropping = False
            skipping_profiles = False
            out.append(line)
            continue
        if in_services:
            m = _SERVICE_RE.match(line)
            if m:
                dropping = m.group(1) not in keep
                skipping_profiles = False
                if not dropping and dep and m.group(1) == app:
                    out.append(line)
                    out.append(f"    depends_on: [{dep}]")
                    continue
            if dropping:
                continue
            stripped = line.strip()
            if re.match(r"^profiles:\s*(\[.*\])?\s*$", stripped):
                skipping_profiles = stripped.endswith("profiles:")   # block list follows
                continue
            if skipping_profiles:
                if stripped.startswith("- "):
                    continue
                skipping_profiles = False
            # A service that is gone must leave the others' depends_on too, or
            # compose refuses the file. Flow form ([a, b]) and block form.
            if gone:
                fm = re.match(r"^(\s*depends_on:\s*)\[(.*)\]\s*$", line)
                if fm:
                    left = [x.strip() for x in fm.group(2).split(",") if x.strip() and x.strip() not in gone]
                    if not left:
                        continue
                    out.append(f"{fm.group(1)}[{', '.join(left)}]")
                    continue
                if re.match(r"^\s*depends_on:\s*$", line):
                    in_depends = True
                    depends_header = line
                    depends_kept = 0
                    continue
                if in_depends:
                    dm = re.match(r"^\s*-\s*([A-Za-z0-9_.-]+)\s*$", line)
                    if dm:
                        if dm.group(1) in gone:
                            continue
                        if depends_kept == 0:
                            out.append(depends_header)
                        depends_kept += 1
                    else:
                        in_depends = False
        out.append(line)
    if in_services and option and option.service_yaml and not inserted:
        out.append(option.service_yaml.rstrip("\n"))
        inserted = True
    result = "\n".join(out).rstrip("\n") + "\n"
    result = re.sub(r"\n{3,}", "\n\n", result)
    want = set(keep)
    if option and option.service_yaml:
        want.add(_SERVICE_RE.match(option.service_yaml.splitlines()[0]).group(1))
    got = set(((yaml.safe_load(result) or {}).get("services") or {}).keys())
    if got != want:
        label = ", ".join(o.id for o in options) or "default"
        raise ValueError(f"flattening for '{label}' kept {sorted(got)}, "
                         f"wanted {sorted(want)}; the compose's services need two-space indents")
    return result


def env_with_options(example_env: str, pairs: list[tuple[Choice, Option]]) -> str:
    """example.env with every (choice, option) pair applied in turn."""
    out = example_env
    for c, o in pairs:
        out = env_with_option(out, c, o)
    return out


def variant_suffix(pairs: list[tuple[Choice, Option]]) -> str:
    """``<choice>-<option>`` per non-default pair, joined by dots; "" when
    every pair is its choice's default."""
    return ".".join(f"{c.id}-{o.id}" for c, o in pairs if o.id != c.default)


def env_with_option(example_env: str, choice: Choice, option: Option) -> str:
    """example.env with the option's values applied and its asks listed.

    A key already in the file is replaced in place; the rest lands under a
    heading for the option. Asked values are left empty, their label as the
    comment, so a reader sees what to fill in.
    """
    lines = example_env.rstrip("\n").splitlines() if example_env.strip() else []
    pending: list[tuple[str, str, str]] = []  # (key, value, comment)
    for k, v in option.env.items():
        pending.append((k, v, ""))
    for ln in option.env_lines:
        k, _, rest = ln.partition("=")
        v, _, comment = rest.partition("  # ")
        pending.append((k, v, comment))
    for a in option.ask:
        if any(a.name == k for k, _, _ in pending):
            continue
        comment = a.label or a.name
        if a.values:
            comment += ": " + " | ".join(a.values.keys())
        pending.append((a.name, a.default, comment))
    remaining: list[tuple[str, str, str]] = []
    for k, v, comment in pending:
        done = False
        for i, ln in enumerate(lines):
            if re.match(rf"^#?\s*{re.escape(k)}=", ln):
                lines[i] = f"{k}={v}" + (f"  # {comment}" if comment else "")
                done = True
                break
        if not done:
            remaining.append((k, v, comment))
    if remaining:
        if lines:
            lines.append("")
        lines.append(f"# {choice.label}: {option.label}")
        for k, v, comment in remaining:
            lines.append(f"{k}={v}" + (f"  # {comment}" if comment else ""))
    return "\n".join(lines) + "\n"


def variant_names(choice: Choice, option: Option) -> tuple[str, str]:
    """(compose file, env file) for a non-default option."""
    suffix = f"{choice.id}-{option.id}"
    return f"compose.{suffix}.yaml", f"example.{suffix}.env"
