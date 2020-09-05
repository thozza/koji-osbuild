#!/usr/bin/python3
import enum
import json
import sys
import time
import urllib.parse
import urllib.request

from string import Template
from typing import Dict, List
import koji

from koji.tasks import BaseTaskHandler


class Repository:
    def __init__(self, baseurl: str, gpgkey: str = None):
        self.baseurl = baseurl
        self.gpgkey = gpgkey

    def as_dict(self, arch: str = ""):
        tmp = Template(self.baseurl)
        url = tmp.substitute(arch=arch)
        res = {"baseurl": url}
        if self.gpgkey:
            res["gpgkey"] = self.gpgkey
        return res


class ImageRequest:
    def __init__(self, arch: str, image_type: str, repos: List):
        self.architecture = arch
        self.image_type = image_type
        self.repositories = repos

    def as_dict(self):
        arch = self.architecture
        return {
            "architecture": self.architecture,
            "image_type": self.image_type,
            "repositories": [
                repo.as_dict(arch) for repo in self.repositories
            ]
        }


class ComposeRequest:
    def __init__(self, distro: str, images: ImageRequest, koji: str):
        self.distribution = distro
        self.image_requests = images
        self.koji = koji

    def as_dict(self):
        return {
            "distribution": self.distribution,
            "koji": {
                "server": str(self.koji)
            },
            "image_requests": [
                img.as_dict() for img in self.image_requests
            ]
        }

    def to_json(self, encoding=None):
        data = json.dumps(self.as_dict())
        if encoding:
            data = data.encode('utf-8')
        return data


class ImageStatus(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    BUILDING = "building"
    UPLOADING = "uploading"
    WAITING = "waiting"
    FINISHED = "finished"
    RUNNING = "running"


class ComposeStatus:
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    REGISTERING = "registering"
    FINISHED = "finished"

    def __init__(self, status: str, images: List, koji_task_id: str):
        self.status = status
        self.images = images
        self.koji_task_id = koji_task_id

    @classmethod
    def from_dict(cls, data: Dict):
        status = data["status"].lower()
        koji_task_id = data["koji_task_id"]
        images = [ImageStatus(s["status"].lower()) for s in data["image_statuses"]]
        return cls(status, images, koji_task_id)

    @property
    def is_finished(self):
        if self.is_success:
            return True
        return self.status in [self.FAILED]

    @property
    def is_success(self):
        return self.status in [self.SUCCESS, self.FINISHED]


class Client:
    def __init__(self, url):
        self.url = url

    def compose_create(self, distro: str, images: List[ImageRequest], koji: str):
        url = urllib.parse.urljoin(self.url, f"/compose")
        req = urllib.request.Request(url)
        cro = ComposeRequest(distro, images, koji)
        dat = json.dumps(cro.as_dict())
        raw = dat.encode('utf-8')
        req = urllib.request.Request(url, raw)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Content-Length', len(raw))

        with urllib.request.urlopen(req, raw) as res:
            payload = res.read().decode('utf-8')
        ps = json.loads(payload)
        compose_id = ps["id"]
        return compose_id

    def compose_status(self, compose_id: str):
        url = urllib.parse.urljoin(self.url, f"/compose/{compose_id}")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as res:
            data = res.read().decode('utf-8')
        js = json.loads(data)

        return ComposeStatus.from_dict(js)

    def wait_for_compose(self, compose_id: str, *, sleep_time=2):
        while True:
            status = self.compose_status(compose_id)
            if status.is_finished:
                return status

            time.sleep(sleep_time)


class OSBuildImage(BaseTaskHandler):
    Methods = ['osbuildImage']
    _taskWeight = 2.0

    def __init__(self, task_id, method, params, session, options):
        super().__init__(task_id, method, params, session, options)

        self.composer_url = "http://composer:8701/"
        self.koji_url = "https://localhost/kojihub"
        self.client = Client(self.composer_url)

    def handler(self, name, version, arches, target, opts):
        self.logger.debug("Building image via osbuild %s, %s, %s, %s",
                          name, str(arches), str(target), str(opts))

        #self.logger.debug("Event id: %s", str(self.event_id))

        #target_info = self.session.getBuildTarget(target, strict=True)
        #build_tag = target_info['build_tag']
        #repo_info = self.getRepo(build_tag)
        #buildconfig = self.session.getBuildConfig(build_tag)

        #if repo_info:
        #    self.logger.debug("repo info: %s", str(repo_info))

        #if buildconfig:
        #    self.logger.debug("build-config: %s", str(buildconfig))

        client = self.client

        distro = f"{name}-{version}"
        images = []
        formats = ["qcow2"]
        repo_url = "http://download.fedoraproject.org/pub/fedora/linux/releases/32/Everything/$arch/os/"
        repos = [Repository(repo_url)]
        for fmt in formats:
            for arch in arches:
                ireq = ImageRequest(arch, fmt, repos)
                images.append(ireq)

        self.logger.debug("Creating compose: %s\n  koji: %s\n  images: %s",
                          distro, self.koji_url,
                          str([i.as_dict() for i in images]))

        cid = client.compose_create(distro, images, self.koji_url)
        self.logger.info("Compose id: %s", cid)

        self.logger.debug("Waiting for comose to finish")
        status = client.wait_for_compose(cid)

        if not status.is_success:
            self.logger.error("Compose failed: %s", str(status))
            return {
                'koji_builds': []
            }

        return {
            'koji_builds': [],
            'build': f'{cid}-1-1',
        }


# #####
import argparse

RESET = "\033[0m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RED = "\033[31m"


def show_compose(cs):
    print(f"status: {BOLD}{cs.status}{RESET}")
    print("koji task: " + str(cs.koji_task_id))
    print("images: ")
    for image in cs.images:
        print("  " + str(image))


def compose_cmd(client: Client, args):
    images = []
    formats = args.format or ["qcow2"]
    repos = [Repository(url) for url in args.repo]
    for fmt in formats:
        for arch in args.arch:
            ireq = ImageRequest(arch, fmt, repos)
            images.append(ireq)
    cid = client.compose_create(args.distro, images, args.koji)

    print(f"Compose: {cid}")
    while True:
        status = client.compose_status(cid)
        print(f"status: {status.status: <10}\r", end="")
        if status.is_finished:
            break

        time.sleep(2)
    show_compose(status)


def status_cmd(client: Client, args):
    cs = client.compose_status(args.id)
    show_compose(cs)


def wait_cmd(client: Client, args):
    cs = client.wait_for_compose(args.id)
    show_compose(cs)


def main():
    parser = argparse.ArgumentParser(description="osbuild composer koji API client")
    parser.add_argument("--url", metavar="URL", type=str,
                        default="http://localhost:8701/",
                        help="The URL of the osbuild composer koji API endpoint")
    parser.set_defaults(cmd=None)
    sp = parser.add_subparsers(help='commands')

    subpar = sp.add_parser("compose", help='create a new compose')
    subpar.add_argument("distro", metavar="NAME", help='The distribution')
    subpar.add_argument("repo", metavar="REPO", help='The repository to use',
                         type=str, action="append", default=[])
    subpar.add_argument("arch", metavar="ARCHITECTURE", help='Request the architecture',
                         type=str, nargs="+")
    subpar.add_argument("--koji", metavar="URL", help='The koji url',
                        default="https://localhost/kojihub")
    subpar.add_argument("--format", metavar="FORMAT", help='Request the image format [qcow2]',
                        action="append", type=str, default=[])
    subpar.set_defaults(cmd='compose')

    subpar = sp.add_parser("status", help='status of a compose')
    subpar.add_argument("id", metavar="COMPOSE_ID", help='compose id')
    subpar.set_defaults(cmd='status')

    subpar = sp.add_parser("wait", help='wait for a compose')
    subpar.add_argument("id", metavar="COMPOSE_ID", help='compose id')
    subpar.set_defaults(cmd='wait')

    args = parser.parse_args()

    if not args.cmd:
        print(f"{RED}Error{RESET}: Need command\n")
        parser.print_help(sys.stderr)
        return 1

    client = Client(args.url)

    if args.cmd == "compose":
        return compose_cmd(client, args)
    if args.cmd == "status":
        return status_cmd(client, args)
    if args.cmd == "wait":
        return wait_cmd(client, args)
    return 1



if __name__ == "__main__":
    sys.exit(main())
