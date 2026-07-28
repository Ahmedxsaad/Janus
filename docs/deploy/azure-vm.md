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

## What this costs

| | |
|---|---|
| VM (`Standard_B4ms`, 4 vCPU / 16 GiB, burstable) | ~$0.17/hr on-demand, US regions |
| 128 GiB Standard SSD OS disk | ~$9.60/month, prorated hourly |
| Public IP (Standard SKU) | ~$0.004/hr while allocated |

Figures gathered from third-party Azure pricing aggregators in July 2026 and
not independently re-verified against the Azure Portal at guide-writing time;
**check the [Azure pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator/)
before provisioning**, prices change. For the ~15-day judging window, running
continuously: roughly **$60-70 total**, most of it compute. Burstable B-series
was chosen over a general-purpose size like `Standard_D4s_v5` (~$0.19/hr)
specifically because this workload is bursty and mostly idle (booting, then
sitting quiet between judge visits, then a poll loop that periodically calls
out to a local GMS): B-series banks CPU credit during the idle stretches and
spends it during the bursts, which is exactly this shape of demand.

**Azure only bills compute while the VM is running.** `az vm deallocate`
stops the meter (disk and IP costs continue, both small); `az group delete`
stops everything, including disk and IP, and is what you want once the
judging period ends. Both are one command, in [Tear down](#tear-down).

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
  --size Standard_B4ms \
  --admin-username azureuser \
  --generate-ssh-keys \
  --nsg modelguard-demo-nsg \
  --public-ip-sku Standard \
  --custom-data deploy/azure/cloud-init.yaml
```

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

## Tear down

```bash
# Stops billing for compute immediately; disk and IP costs continue, both small.
az vm deallocate --resource-group "$RG" --name "$VM"

# Removes everything in the resource group: the VM, its disk, its NSG, its
# public IP. This is what actually stops the meter once judging ends.
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
