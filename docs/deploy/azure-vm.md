# Hosting a judge-facing demo on an Azure VM

One VM running the full stack (DataHub Quickstart + `modelguard watch`,
continuously) so a judge can open the DataHub UI during the judging period
(Aug 17-31, 2026) and see live incidents, tags, trust scores, and impact
reports, without anyone needing to be online to demonstrate it. This satisfies
the submission rules' requirement to "provide access to a working Project for
judging... available free of charge and without restriction... until the
Judging Period ends" (`docs/hackathon-specs/03-submission-requirements.md`).

**Not verified end to end.** No live Azure VM exists in the environment this
guide was written in: the `az` commands below were checked against the
installed CLI's own `--help` output and cross-referenced against Azure's
current documentation, `deploy/azure/cloud-init.yaml`'s YAML structure and
every `runcmd` shell fragment were syntax-checked, and
`deploy/azure/modelguard-watch.service` passed `systemd-analyze verify`. None
of that proves a real VM boots correctly. **Do the smoke test in [Verify the
demo works](#verify-the-demo-works) before telling anyone this URL is live.**

## The one security decision that matters here

A DataHub Quickstart ships with metadata-service authentication disabled by
default (`.env.example`'s own documented behaviour, and the judge's
out-of-the-box path everywhere else in this repo). That is fine on a laptop.
It is not fine on a box reachable from the public internet: an unauthenticated
GMS answers arbitrary GraphQL writes to anyone who can reach port 8080, no
credential required.

So **GMS (8080) is never opened to the internet.** Only the DataHub frontend
(9002), which has its own login, is exposed publicly. The Network Security
Group below only ever allows 22 and 9002 inbound; `cloud-init.yaml` adds a
host-level `ufw` firewall doing the same thing again, independently, so a
mistake in one layer is not the only thing standing between the internet and
an unauthenticated write API.

**Change the frontend's default password** (`datahub`/`datahub`) before
judging starts if the URL will be shared publicly rather than only in the
submission's testing instructions:

```bash
# From the DataHub UI: Settings (top right) -> Access Tokens is not it; the
# password lives under the user's own profile page, Settings -> Reset password.
# There is no CLI for this in the OSS Quickstart; do it once, in the browser,
# right after first login.
```

## What this costs, and fitting it into a small budget

Two independent levers, and the second one matters more than the first.

**Size.** `Standard_B2ms` (2 vCPU / 8 GiB, burstable) rather than a larger
size: it matches this project's own stated Quickstart requirement ("about 2
CPUs / 8 GB free," `README.md`) almost exactly, at roughly half the hourly
cost of a size with headroom above it (`Standard_B4ms`, 4 vCPU / 16 GiB).
That is a real, honest tradeoff, not a free upgrade: GMS, OpenSearch, and
Kafka are each their own JVM, plus MySQL, plus `modelguard watch`, all
competing for 8 GiB minus OS overhead, and this has not been run on real
hardware to confirm it holds up under load. If the VM OOMs or thrashes, the
fix is `az vm resize` to `Standard_B4ms`, not tuning further; both sizes are
in `values.yaml`-style comments in the commands below so switching is a
one-line change.

**When it runs.** Azure only bills compute while a VM is actually running.
Provisioning today and leaving it up straight through the end of judging
(Aug 31) pays for every idle hour in between, most of which nobody is
looking at it. Provisioning now, testing it, then `az vm deallocate` (stops
the compute meter; the disk and IP stay allocated, so restarting later is one
command, not a re-provision) until shortly before judging starts, is the
same VM for a fraction of the runtime. See [Pause it between now and
judging](#pause-it-between-now-and-judging).

| | Rate | Sourced |
|---|---|---|
| `Standard_B2ms` (2 vCPU / 8 GiB, burstable) | ~$0.083/hr | [Holori](https://calculator.holori.com/azure/vm/standard-b2ms), [Vantage](https://instances.vantage.sh/azure/vm/b2ms) |
| `Standard_B4ms` (4 vCPU / 16 GiB), if B2ms is not enough | ~$0.17/hr | third-party aggregators, cross-checked |
| 64 GiB Standard SSD OS disk | ~$4.80/month, prorated hourly, bills even while stopped | scaled from the 128 GiB rate (~$9.60/month) |
| Public IP (Standard SKU) | ~$0.004/hr while allocated, including while stopped | third-party aggregators |

Gathered via live search in July 2026, not independently re-verified against
the Azure Portal at guide-writing time; **check the [Azure pricing
calculator](https://azure.microsoft.com/en-us/pricing/calculator/) before
provisioning**, prices change and vary by region.

**Worked example**, provisioning July 29 for a submission around Aug 10 and
judging Aug 17-31: roughly 2 days of testing/demo-recording plus the ~14.3-day
judging window is about 390 compute-hours, `B2ms` at that rate is ~$32; the
disk and IP run the full ~33 days regardless of VM state, about $5 and $3.
**Total: roughly $40**, against a $60 budget. Leaving `B4ms` running
continuously for the same 33 days instead costs roughly $131 on compute
alone, over double the budget on size and runtime choice alone.

## Provision it

Prerequisites: the [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
installed and `az login` already run.

```bash
RG=modelguard-demo
LOCATION=eastus
VM=modelguard-demo-vm

az group create --name "$RG" --location "$LOCATION"

# A dedicated NSG, not the default one az vm create would generate, because
# the default only ever opens the SSH port and this VM also needs 9002 open
# to the world while 8080 must never be. Built explicitly so both rules are
# visible in one place rather than guessed at.
az network nsg create --resource-group "$RG" --name modelguard-demo-nsg

# Scoped to the machine provisioning it, not to the internet: SSH access is
# for setup and troubleshooting, not something a judge needs. Update the
# source prefix if provisioning from a different network later
# (az network nsg rule update ... --source-address-prefixes <new-ip>/32).
MY_IP=$(curl -s https://ifconfig.me)
az network nsg rule create \
  --resource-group "$RG" --nsg-name modelguard-demo-nsg \
  --name AllowSSHFromMe --priority 100 \
  --source-address-prefixes "${MY_IP}/32" --destination-port-ranges 22 \
  --access Allow --protocol Tcp

# The DataHub UI, open to the world: this is the one thing a judge visits.
az network nsg rule create \
  --resource-group "$RG" --nsg-name modelguard-demo-nsg \
  --name AllowDataHubUI --priority 110 \
  --source-address-prefixes "*" --destination-port-ranges 9002 \
  --access Allow --protocol Tcp

# No rule for 8080, 3306, 9092, or 9200: Azure NSGs deny by default, so
# absence of a rule here is the control, not an oversight to double check.

az vm create \
  --resource-group "$RG" --name "$VM" \
  --image Canonical:ubuntu-24_04-lts:server:latest \
  --size Standard_B2ms \
  --os-disk-size-gb 64 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --nsg modelguard-demo-nsg \
  --public-ip-sku Standard \
  --custom-data deploy/azure/cloud-init.yaml
```

Tight on RAM if it OOMs or the frontend starts feeling sluggish under real use
(see [What this costs](#what-this-costs-and-fitting-it-into-a-small-budget)):
resize in place rather than reprovisioning, `az vm deallocate` first since resize
needs the VM stopped, then `az vm resize --resource-group "$RG" --name "$VM"
--size Standard_B4ms`, then `az vm start`. The disk, the NSG, the IP, and
everything cloud-init already did all survive a resize untouched.

`az vm create` returns once the VM exists, not once cloud-init has finished
provisioning it: first boot pulls container images, extracts GMS's WAR, and
runs a full `modelguard-seed` against a graph that has to be reachable first.
Expect several minutes before the DataHub UI answers.

```bash
az vm show --resource-group "$RG" --name "$VM" -d --query publicIps -o tsv
```

## Verify the demo works

```bash
ssh azureuser@<public-ip>

# First thing to check if anything below looks wrong: this is where every
# runcmd step's real output landed, in order, including the exact point of
# any failure.
sudo tail -100 /var/log/cloud-init-output.log

systemctl status modelguard-watch.service
curl -s http://localhost:8080/config   # GMS answering locally
```

Then from a browser: `http://<public-ip>:9002`, log in (`datahub`/`datahub`
unless already changed), and look for the `loans_raw` incident, the
`credit_risk_v3` model's `model-at-risk` tag and trust score, and the guarding
assertion, exactly as `README.md`'s own "Try it" walkthrough describes them
locally.

## Pause it between now and judging

The budget-fitting move from [What this costs](#what-this-costs-and-fitting-it-into-a-small-budget):
provision once, test it, submit, then stop paying for compute until shortly
before judging actually starts.

```bash
# Stops the compute meter. The disk, the NSG, and the public IP all stay
# allocated (their own small, unavoidable cost, see the table above), so
# starting again is this one command, not a re-provision.
az vm deallocate --resource-group "$RG" --name "$VM"
```

Shortly before Aug 17, 10:00am ET:

```bash
az vm start --resource-group "$RG" --name "$VM"
```

`modelguard-watch.service` is `enabled` (not just `started`), so it comes
back on its own once the VM boots; no need to SSH in and restart anything.
Give it a few minutes for DataHub's own containers to come up first (`docker
compose ps` over SSH, or just watch port 9002 start answering), same as after
the very first boot.

## Tear down

Once judging ends (Aug 31, 5:00pm ET):

```bash
# Removes everything in the resource group: the VM, its disk, its NSG, its
# public IP. Unlike deallocate, this actually stops every meter, including
# the small ones that ran the whole time the VM was paused.
az group delete --resource-group "$RG" --yes --no-wait
```

## Files

- [`deploy/azure/cloud-init.yaml`](../../deploy/azure/cloud-init.yaml) - first-boot
  provisioning: Docker, this repo, DataHub Quickstart, the seeded demo graph,
  `modelguard-watch.service`.
- [`deploy/azure/modelguard-watch.service`](../../deploy/azure/modelguard-watch.service) -
  the systemd unit cloud-init installs. Watches both `loans_raw` (freshness)
  and `credit_risk_v3` (leakage) continuously; `Restart=always` with a 15s
  backoff rather than any ordering dependency on DataHub's own startup, since
  `modelguard watch` already fails fast and loudly when GMS is not yet
  reachable (the same exit-code discipline `modelguard gate` relies on,
  reused here rather than re-invented).
