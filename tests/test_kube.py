"""The Kubernetes CIT backend's pure parts: pod names, pod specs, pod state."""

import unittest

from dbuild import kube


class TestPodName(unittest.TestCase):
    def test_cit_name_is_a_dns_label(self):
        self.assertEqual(kube.pod_name("cit-4242-nginx-php-fpm"), "cit-4242-nginx-php-fpm")
        self.assertEqual(kube.pod_name("cit-1-Foo_Bar.baz"), "cit-1-foo-bar-baz")
        self.assertLessEqual(len(kube.pod_name("cit-1-" + "x" * 80)), 63)


class TestPodSpec(unittest.TestCase):
    def test_annotations_env_and_args_land_where_kubelet_reads_them(self):
        spec = kube.pod_spec(
            "cit-1-postgres", "ghcr.io/daemonless/postgres:latest",
            annotations={"org.freebsd.jail.allow.sysvipc": "true"},
            env={"POSTGRES_PASSWORD": "x", "PUID": 1000},
            args=["--version"],
        )
        self.assertEqual(spec["metadata"]["namespace"], kube.NAMESPACE)
        self.assertEqual(spec["metadata"]["annotations"], {"org.freebsd.jail.allow.sysvipc": "true"})
        self.assertEqual(spec["spec"]["restartPolicy"], "Never")
        c = spec["spec"]["containers"][0]
        self.assertEqual(c["image"], "ghcr.io/daemonless/postgres:latest")
        self.assertEqual(c["env"], [{"name": "POSTGRES_PASSWORD", "value": "x"}, {"name": "PUID", "value": "1000"}])
        self.assertEqual(c["args"], ["--version"])
        self.assertNotIn("securityContext", c)

    def test_bare_spec_has_no_env_or_args(self):
        c = kube.pod_spec("cit-1-redis", "ghcr.io/daemonless/redis:latest")["spec"]["containers"][0]
        self.assertNotIn("env", c)
        self.assertNotIn("args", c)


class TestContainerState(unittest.TestCase):
    def pod(self, state):
        return {"status": {"phase": "Running", "containerStatuses": [{"state": state}]}}

    def test_states(self):
        self.assertEqual(kube._container_state(self.pod({"running": {}})), ("running", ""))
        self.assertEqual(kube._container_state(self.pod({"waiting": {"reason": "ImagePullBackOff"}})), ("waiting", "ImagePullBackOff"))
        self.assertEqual(kube._container_state(self.pod({"terminated": {"exitCode": 3}})), ("terminated", ""))
        self.assertEqual(kube._container_state({"status": {}}), ("", ""))


class TestInterpolate(unittest.TestCase):
    def test_compose_forms(self):
        env = {"TZ": "Europe/Oslo", "EMPTY": ""}
        self.assertEqual(kube.interpolate("${TZ}", env), "Europe/Oslo")
        self.assertEqual(kube.interpolate("${MISSING:-UTC}", env), "UTC")
        self.assertEqual(kube.interpolate("${EMPTY:-x}", env), "x")
        self.assertEqual(kube.interpolate("${EMPTY-x}", env), "")
        self.assertEqual(kube.interpolate("redis://${HOST:-localhost}:6379", env), "redis://localhost:6379")
        self.assertEqual(kube.interpolate("${MISSING}", env), "")

    def test_env_file(self):
        self.assertEqual(kube.parse_env_file("# c\nA=1\nB='two'\n\nC=\"3\"\nnoeq\n"), {"A": "1", "B": "two", "C": "3"})


STACK = {
    "services": {
        "paperless-ngx": {
            "image": "ghcr.io/daemonless/paperless-ngx:${TAG:-latest}",
            "network_mode": "host",
            "environment": ["PUID=1000", "PAPERLESS_REDIS=redis://${REDIS_HOSTNAME:-localhost}:6379"],
            "env_file": [".env"],
            "depends_on": ["redis"],
            "volumes": ["${CONFIG_LOCATION}:/config", "/etc/localtime:/etc/localtime:ro"],
            "ports": ["8000:8000"],
        },
        "redis": {
            "image": "ghcr.io/daemonless/redis:latest",
            "network_mode": "host",
            "annotations": {"org.freebsd.jail.allow.sysvipc": "true"},
            "volumes": [{"type": "bind", "source": "${REDIS_DATA_LOCATION}", "target": "/config"}],
        },
        "proxy": {"image": "x", "profiles": ["proxy"]},
    }
}


class TestStackSpec(unittest.TestCase):
    def test_one_pod_with_a_container_per_service(self):
        env = {"CONFIG_LOCATION": "/containers/paperless-ngx/config", "REDIS_DATA_LOCATION": "/containers/paperless-ngx/redis", "SECRET": "s"}
        spec, images = kube.stack_spec(
            "cit-1-paperless-ngx", STACK, env=env, env_files={".env": env}, data_root="/var/tmp/cit",
            image_map={"ghcr.io/daemonless/paperless-ngx:latest": "ghcr.io/daemonless/paperless-ngx:2.20"},
        )
        self.assertEqual(images, {"paperless-ngx": "ghcr.io/daemonless/paperless-ngx:2.20", "redis": "ghcr.io/daemonless/redis:latest"})
        names = [c["name"] for c in spec["spec"]["containers"]]
        self.assertEqual(names, ["paperless-ngx", "redis"])
        self.assertEqual(spec["spec"]["restartPolicy"], "Always")
        self.assertEqual(spec["metadata"]["annotations"], {"org.freebsd.jail.allow.sysvipc": "true"})
        app = spec["spec"]["containers"][0]
        env_map = {e["name"]: e["value"] for e in app["env"]}
        self.assertEqual(env_map["PAPERLESS_REDIS"], "redis://localhost:6379")
        self.assertEqual(env_map["SECRET"], "s")
        self.assertEqual(env_map["PUID"], "1000")
        self.assertEqual([m["mountPath"] for m in app["volumeMounts"]], ["/config"])
        paths = {v["name"]: v["hostPath"]["path"] for v in spec["spec"]["volumes"]}
        self.assertEqual(paths[app["volumeMounts"][0]["name"]], "/var/tmp/cit/paperless-ngx/containers/paperless-ngx/config")
        redis = spec["spec"]["containers"][1]
        self.assertEqual(redis["volumeMounts"][0]["mountPath"], "/config")
        self.assertTrue(all(v["hostPath"]["type"] == "DirectoryOrCreate" for v in spec["spec"]["volumes"]))


BRIDGED = {
    "services": {
        "photocraft": {"image": "ghcr.io/daemonless/photocraft:latest", "ports": ["8080:80"]},
        "vectorcraft": {"image": "ghcr.io/daemonless/vectorcraft:latest", "ports": ["8081:80"],
                        "annotations": {"org.freebsd.jail.allow.mlock": "true"}},
        "db": {"image": "ghcr.io/daemonless/postgres:latest", "volumes": ["dbdata:/var/lib/postgresql/data"]},
    }
}


class TestStackShapes(unittest.TestCase):
    def test_host_network_is_one_pod_and_bridged_is_not(self):
        self.assertTrue(kube.shares_host_network(STACK))
        self.assertFalse(kube.shares_host_network(BRIDGED))
        self.assertFalse(kube.shares_host_network({"services": {}}))

    def test_published_ports(self):
        self.assertEqual(kube.published_ports({"ports": ["8080:80", "9000", "127.0.0.1:8443:443/tcp"]}, {}), {8080: 80, 9000: 9000, 8443: 443})
        self.assertEqual(kube.published_ports({"ports": [{"published": 8100, "target": 80}]}, {}), {8100: 80})
        self.assertEqual(kube.published_ports({"ports": ["${WEB_PORT:-8200}:80"]}, {}), {8200: 80})

    def test_pod_and_service_per_bridged_service(self):
        specs, _images, ports = kube.stack_specs_per_service("cit-1-crafting-apps", BRIDGED, env={}, data_root="/var/tmp/cit")
        kinds = [(sp["kind"], sp["metadata"]["name"]) for sp in specs]
        self.assertEqual(kinds, [
            ("Pod", "cit-1-crafting-apps-photocraft"), ("Service", "photocraft"),
            ("Pod", "cit-1-crafting-apps-vectorcraft"), ("Service", "vectorcraft"),
            ("Pod", "cit-1-crafting-apps-db"),
        ])
        self.assertEqual(ports["photocraft"], {8080: 80})
        self.assertEqual(ports["db"], {})
        pod = specs[2]
        self.assertEqual(pod["metadata"]["annotations"], {"org.freebsd.jail.allow.mlock": "true"})
        self.assertEqual(pod["metadata"]["labels"]["dbuild-cit/stack"], "cit-1-crafting-apps")
        self.assertEqual(pod["spec"]["hostname"], "vectorcraft")
        svc = specs[3]
        self.assertEqual(svc["spec"]["selector"], {"dbuild-cit/service": "vectorcraft", "dbuild-cit/stack": "cit-1-crafting-apps"})
        self.assertEqual(svc["spec"]["ports"], [{"name": "p80", "port": 80, "targetPort": 80}])
        self.assertEqual(specs[4]["spec"]["volumes"][0]["hostPath"]["path"], "/var/tmp/cit/vol-dbdata")
