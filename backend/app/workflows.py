"""M5 node-maintenance workflows: step definitions + built-in playbooks.

A workflow is an ordered list of steps. Each step runs as one ansible job
(via runner_service.run_playbook), which gives per-step logs for free.

Steps that need the Kubernetes API (cordon/drain/uncordon/k8s verify and the
k8s preflight checks) run on localhost with kubectl and a kubeconfig supplied
at run creation. When no kubeconfig is given, k8s steps are skipped and the
run degrades to a pure OS-level rolling workflow.
"""

PREFLIGHT_YML = """\
- name: maintenance preflight
  hosts: all
  gather_facts: false
  tasks:
    - name: check ssh connectivity
      ansible.builtin.ping:

    - name: check disk space
      ansible.builtin.shell: df -P / | awk 'NR==2 {print $5}' | tr -d '%'
      register: disk_used
      changed_when: false

    - name: assert enough free disk
      ansible.builtin.assert:
        that: disk_used.stdout | int < 80
        fail_msg: "disk usage {{ disk_used.stdout }}% >= 80%"
"""

PREFLIGHT_K8S_YML = """\
- name: kubernetes preflight
  hosts: localhost
  gather_facts: false
  tasks:
    - name: check control plane health
      ansible.builtin.shell: kubectl get nodes --no-headers | awk '$2 != "Ready" {print $1}'
      register: not_ready
      changed_when: false
      failed_when: false

    - name: assert control plane ready
      ansible.builtin.assert:
        that: not_ready.stdout == ""
        fail_msg: "nodes not Ready: {{ not_ready.stdout }}"

    - name: check version skew
      ansible.builtin.shell: kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.status.nodeInfo.kubeletVersion}{"\\n"}{end}'
      register: versions
      changed_when: false

    - name: report version skew
      ansible.builtin.debug:
        msg: "WARNING: kubelet versions in cluster:\\n{{ versions.stdout }}"
      when: versions.stdout_lines | length > 1

    - name: check single-replica workloads
      ansible.builtin.shell: >
        kubectl get deploy,sts --all-namespaces -o json |
        python3 -c "import json,sys; d=json.load(sys.stdin);
        [print(i['metadata']['namespace']+'/'+i['metadata']['name'])
         for i in d['items'] if (i['spec'].get('replicas',1) or 1) < 2]"
      register: single
      changed_when: false
      failed_when: false

    - name: report single-replica workloads
      ansible.builtin.debug:
        msg: "WARNING: single-replica workloads (no HA during maintenance):\\n{{ single.stdout }}"
      when: single.stdout != ""
"""

CORDON_YML = """\
- name: cordon node
  hosts: localhost
  gather_facts: false
  tasks:
    - name: cordon node
      ansible.builtin.shell: kubectl cordon {{ k8s_node }}
"""

DRAIN_YML = """\
- name: drain node
  hosts: localhost
  gather_facts: false
  tasks:
    - name: drain node
      ansible.builtin.shell: >
        kubectl drain {{ k8s_node }} --ignore-daemonsets
        --delete-emptydir-data --timeout={{ drain_timeout | default(300) }}s
"""

OS_PATCH_YML = """\
- name: os patch
  hosts: all
  gather_facts: false
  tasks:
    - name: update apt cache
      ansible.builtin.apt:
        update_cache: true
        cache_valid_time: 3600
      become: true

    - name: upgrade security packages
      ansible.builtin.apt:
        upgrade: safe
      become: true
      register: upgrade_out

    - name: check reboot required
      ansible.builtin.stat:
        path: /var/run/reboot-required
      register: reboot_required
"""

KUBELET_UPGRADE_YML = """\
- name: kubelet upgrade
  hosts: all
  gather_facts: false
  tasks:
    - name: install pinned kubelet version
      ansible.builtin.apt:
        name:
          - kubelet={{ kubelet_version }}
          - kubectl={{ kubelet_version }}
        allow_downgrade: true
      become: true

    - name: restart kubelet
      ansible.builtin.systemd:
        name: kubelet
        state: restarted
        daemon_reload: true
      become: true
"""

VERIFY_SSH_YML = """\
- name: verify node reachable
  hosts: all
  gather_facts: false
  tasks:
    - name: ping node
      ansible.builtin.ping:

    - name: report uptime
      ansible.builtin.shell: uptime -p
      changed_when: false
"""

VERIFY_K8S_YML = """\
- name: verify node ready
  hosts: localhost
  gather_facts: false
  tasks:
    - name: wait for node Ready
      ansible.builtin.shell: kubectl wait --for=condition=Ready node/{{ k8s_node }} --timeout={{ verify_timeout | default(300) }}s
"""

UNCORDON_YML = """\
- name: uncordon node
  hosts: localhost
  gather_facts: false
  tasks:
    - name: uncordon node
      ansible.builtin.shell: kubectl uncordon {{ k8s_node }}
"""

# step_name -> {playbook, on_localhost, needs_k8s}
STEPS = {
    "preflight":     {"playbook": PREFLIGHT_YML,     "localhost": False, "needs_k8s": False},
    "preflight_k8s": {"playbook": PREFLIGHT_K8S_YML, "localhost": True,  "needs_k8s": True},
    "cordon":        {"playbook": CORDON_YML,        "localhost": True,  "needs_k8s": True},
    "drain":         {"playbook": DRAIN_YML,         "localhost": True,  "needs_k8s": True},
    "maintain":      {"playbook": None,              "localhost": False, "needs_k8s": False},  # per workflow
    "verify":        {"playbook": None,              "localhost": False, "needs_k8s": False},  # per workflow
    "uncordon":      {"playbook": UNCORDON_YML,      "localhost": True,  "needs_k8s": True},
}

WORKFLOWS = {
    "os-patch": {
        "label": "OS patch",
        "maintain_playbook": OS_PATCH_YML,
        "verify_playbook": VERIFY_SSH_YML,
        "node_steps": ["cordon", "drain", "maintain", "verify", "uncordon"],
    },
    "kubelet-upgrade": {
        "label": "Kubelet upgrade",
        "maintain_playbook": KUBELET_UPGRADE_YML,
        "verify_playbook": VERIFY_K8S_YML,
        "node_steps": ["cordon", "drain", "maintain", "verify", "uncordon"],
    },
}

# ordered node states for the UI pipeline
NODE_STEP_ORDER = ["cordon", "drain", "maintain", "verify", "uncordon"]


def node_steps_for(workflow: str, has_k8s: bool) -> list[str]:
    """Steps for one node; k8s steps are dropped when no kubeconfig was given."""
    steps = list(WORKFLOWS[workflow]["node_steps"])
    if not has_k8s:
        steps = [s for s in steps if not STEPS[s]["needs_k8s"]]
    return steps


def step_playbook(workflow: str, step: str) -> str:
    if step == "maintain":
        return WORKFLOWS[workflow]["maintain_playbook"]
    if step == "verify":
        return WORKFLOWS[workflow]["verify_playbook"]
    return STEPS[step]["playbook"]
