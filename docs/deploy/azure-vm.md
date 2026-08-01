# Hosting a judge-facing demo on an Azure VM

One VM running the full stack (DataHub Quickstart + `modelguard watch`,
continuously) so a judge can open the DataHub UI during the judging period
(Aug 17-31, 2026) and see live incidents, tags, trust scores, and impact
reports, without anyone needing to be online to demonstrate it. This satisfies
the submission rules' requirement to "provide access to a working Project for
judging... available free of charge and without restriction... until the
Judging Period ends" (`docs/hackathon-specs/03-submission-requirements.md`).

**Verified live**, 2026-07-30, on `Standard_B2as_v2` in `francecentral`:
provisioning, seeding, the watch service finding real incidents, the NSG and
`ufw` layers, the frontend password change and an actual login, a real GMS
search query returning real data, and a custom domain over HTTPS
(`https://modelguard.ahmedxsaad.me`) all confirmed working end to end, each
checked directly over SSH rather than assumed from the UI loading. Two real
bugs were found and fixed this way that no amount of syntax-checking would
have caught:

- D-063: `write_files` racing `azureuser`'s own creation, cascading into a
  failed `git clone`.
- D-065: this VM's 8GB RAM is shared across the whole DataHub stack plus
  `modelguard-watch`, and OpenSearch OOM-crashed under that pressure with no
  restart policy, so it stayed dead for 6 hours while GMS and the frontend
  kept answering health checks, silently breaking search/browse the whole
  time. `cloud-init.yaml` now sets `restart: unless-stopped` on every
  `datahub-*` container so a crash self-heals in seconds instead. This is a
  mitigation, not a capacity fix: the VM can still run tight under
  concurrent load, and a recurrence is possible during judging.

**The from-scratch gap is now closed too** (D-066, 2026-07-30): the VM was
deleted and recreated via the Portal wizard from the current, fixed
`cloud-init.yaml`, with a fresh `azureuser`, a fresh OS disk, and a new
public IP (the static IP's zone didn't match this deployment, so DNS was
repointed rather than reattaching it). Cold-init finished in 557 seconds with
zero errors: Docker installed, the repo cloned, the full Quickstart stack
booted, seeding and the scenario ran, and `modelguard-watch.service` raised a
real incident within seconds of boot. All 7 `datahub-*` containers, including
OpenSearch, came up healthy with `restart: unless-stopped` already applied,
confirming D-065's fix actually takes effect on a cold boot and not just when
patched onto a running VM. The two manual post-steps (Caddy/HTTPS, the
frontend password) still need to be redone after any fresh provision, since
neither is in `cloud-init.yaml` by design; both were redone here and verified
with a real `POST /logIn` and an external HTTPS probe, both returning 200.

**Still do the smoke test in [Verify the demo works](#verify-the-demo-works)**
on any new VM regardless: this file describes what worked on the VMs it has
actually been run on, not a guarantee for all future provisions.

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
submission's testing instructions. **The in-app "Reset password" flow does
not work on a bare Quickstart**: it fails with `Failed to generate password
reset token for user`, because that flow signs a reset token with
`DATAHUB_TOKEN_SERVICE_SIGNING_KEY`, which Quickstart leaves unset (confirmed
live: `docker compose ps` warns it is defaulting to a blank string). The
credential actually lives in a flat file baked into the frontend container,
independent of GMS entirely:

```bash
ssh azureuser@<public-ip>

# Overwrite the credential (username:password, plain text) and restart the
# container so it re-reads jaas.conf, which only loads this file at startup.
docker exec datahub-frontend-quickstart-1 sh -c \
  'echo "datahub:<new-password>" > /datahub-frontend/conf/user.props'
docker restart datahub-frontend-quickstart-1
```

Lives only in that file on that VM; there is no recovery path if lost besides
setting it again the same way.

## Cloning a private repo during provisioning

This repo is private (not made public, deliberately, ahead of submission),
so `cloud-init.yaml`'s `git clone` needs a credential; a plain HTTPS clone
with no login fails during an unattended first boot. **Do this only when
provisioning, never commit the result:**

1. Generate a fine-grained GitHub personal access token scoped to exactly
   this one repo, permission **Contents: Read-only**, nothing else. Set its
   expiration past the judging window (past Aug 31, 2026) so it does not
   expire mid-demo, from
   [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).
2. Copy `deploy/azure/cloud-init.yaml`'s contents and replace
   `__GITHUB_CLONE_TOKEN__` with the real token, **in the copy you are about
   to paste into the Azure Portal's Custom Data box, not in the file on
   disk.** `git diff` before provisioning to confirm the tracked file itself
   was never touched.
3. Paste the substituted copy into Custom Data (Advanced tab) and provision
   as normal.
4. Once [Verify the demo works](#verify-the-demo-works) passes, **revoke the
   token** at the same GitHub settings page. The clone already happened; the
   token has no further job, and cloud-init leaves it in plaintext in
   `/var/log/cloud-init-output.log` and its own on-disk state on the VM, so
   revoking it closes that window rather than leaving it open for the rest
   of judging.

If this repo goes public before submission (required by the hackathon rules
regardless, `docs/hackathon-specs/03-submission-requirements.md`), this whole
section stops being necessary: a plain anonymous clone works, and
`__GITHUB_CLONE_TOKEN__` can be dropped back to a bare URL.

## What this costs, and fitting it into a small budget

Two independent levers, and the second one matters more than the first.

**Size.** `Standard_B2as_v2` (2 vCPU / 8 GiB, burstable), on **Regular
(pay-as-you-go), never Spot**. It matches this project's own stated
Quickstart requirement ("about 2 CPUs / 8 GB free," `README.md`) exactly.
D-series (`D2as_v5` and others) looked cheaper in an earlier pass at this
guide (D-060), but that number came from the Azure Portal's VM size picker
while Azure Spot instance was toggled on without our noticing, which shows a
Spot bid price, not the standard rate; D-series also turned out to be
quota-blocked on a student subscription (Spot draws from a separate,
smaller quota pool than standard VMs). `B2as_v2` is what the account already
defaults to and is not quota-blocked. Spot itself is also wrong for this
workload regardless of price: Azure can evict a Spot VM at any moment it
wants the capacity back, and a judge-facing demo that has to stay reachable
through the judging window cannot tolerate an unannounced outage, so this
guide never uses it (D-061). `B2as_v2` is burstable, meaning GMS, OpenSearch,
and Kafka (each their own JVM) plus MySQL plus `modelguard watch` are
competing for banked CPU credit as well as 8 GiB of RAM minus OS overhead;
this has not been run on real hardware to confirm it holds up under load. If
the VM OOMs, throttles, or thrashes, the fix is `az vm resize` to a larger
size in the same family (check quota and current price in the portal first,
not sourced here), not tuning further.

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
| `Standard_B2as_v2` (2 vCPU / 8 GiB, burstable), Regular pricing | $0.07650/hr | Azure Portal, VM size picker, subscription "Azure for Students", region France Central, 2026-07-29, before Spot was toggled on |
| `Standard_B2s_v2` (2 vCPU / 8 GiB, burstable), same specs, no local storage | $0.08496/hr | same portal view |
| 64 GiB Standard SSD OS disk | ~$4.80/month, prorated hourly, bills even while stopped | scaled from the 128 GiB rate (~$9.60/month); not re-checked against the portal |
| Public IP (Standard SKU) | ~$0.004/hr while allocated, including while stopped | third-party aggregators; not re-checked against the portal |

The compute rate came directly from the Azure Portal, not a third-party
aggregator; it is specific to the subscription and region above and **will
differ in a different region, subscription type, or if Azure's pricing
changes**, so re-check the [VM size picker](https://portal.azure.com/) or the
[pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator/)
before provisioning if any of those differ for you. Confirm the picker's
"Display cost" is on Hourly/Regular and that Azure Spot instance is off
before trusting a number from it; a Spot bid price looks like an ordinary
rate but is not one. The disk and public IP rates are still the earlier
web-search estimates, not re-verified against this portal.

**Worked example**, provisioning July 29 for a submission around Aug 10 and
judging Aug 17-31: roughly 2 days of testing/demo-recording plus the ~14.3-day
judging window is about 390 compute-hours, `B2as_v2` at that rate is ~$30;
the disk and IP run the full ~33 days regardless of VM state, about $5 and
$3. **Total: roughly $38**, against a $60 budget.

## Provision it

Prerequisites: the [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
installed and `az login` already run.

```bash
RG=modelguard-demo
LOCATION=francecentral
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
  --size Standard_B2as_v2 \
  --os-disk-size-gb 64 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --nsg modelguard-demo-nsg \
  --public-ip-sku Standard \
  --custom-data deploy/azure/cloud-init.yaml
```

`az vm create` defaults to Regular (pay-as-you-go) pricing; nothing above
opts into Spot, and this guide never should (see [What this
costs](#what-this-costs-and-fitting-it-into-a-small-budget)). `LOCATION` and
the size's price are tied together: the rate above is specific to
`francecentral`. Provisioning in a different region, re-check the VM size
picker there first, prices and quota both vary by region and subscription.

Tight on RAM if it OOMs or the frontend starts feeling sluggish under real use
(see [What this costs](#what-this-costs-and-fitting-it-into-a-small-budget)):
resize in place rather than reprovisioning, `az vm deallocate` first since
resize needs the VM stopped, then `az vm resize --resource-group "$RG" --name
"$VM" --size Standard_B4as_v2` (check quota and current price in the same
portal view first, a family's quota is often shared across its sizes so a
larger size in the same family is not guaranteed to be available even when
the smaller one is; not sourced in this guide), then `az vm start`. The disk,
the NSG, the IP, and everything cloud-init already did all survive a resize
untouched.

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

# Swap must be present: this VM runs tight on 8GB, and with no swap the JVM
# cannot allocate a new thread's native stack during a spike, which is what
# repeatedly killed OpenSearch (D-071). cloud-init.yaml creates this, so an
# empty result here means that step did not run.
swapon --show

# How often the stack has had to self-heal. A non-zero, climbing RestartCount
# on opensearch is the symptom D-071 addressed; it should stop climbing.
sudo docker inspect datahub-opensearch-1 \
  --format 'RestartCount={{.RestartCount}} Policy={{.HostConfig.RestartPolicy.Name}}'
```

Then from a browser: `http://<public-ip>:9002`, log in (`datahub`/`datahub`
unless already changed), and look for the `loans_raw` incident, the
`credit_risk_v3` model's `model-at-risk` tag and trust score, and the guarding
assertion, exactly as `README.md`'s own "Try it" walkthrough describes them
locally.

## Add a custom domain and HTTPS (optional)

Verified live: `https://modelguard.ahmedxsaad.me`, real Let's Encrypt
certificate, no port in the URL. Not part of `cloud-init.yaml` since it needs
a domain name that does not exist at provisioning time; done manually, once,
after the bare-IP demo already works.

1. **DNS**: an A record for the chosen (sub)domain pointing at the VM's
   public IP, **DNS-only, not proxied** (Cloudflare's orange-cloud proxying
   fronts the connection with Cloudflare's own IPs, which breaks the
   `tls-alpn-01` domain-ownership check in step 3, since that check needs a
   direct TLS connection to the VM itself).
2. **Open two more NSG ports**, alongside the existing 22/9002 rules:

   ```bash
   az network nsg rule create \
     --resource-group "$RG" --nsg-name modelguard-demo-nsg \
     --name AllowHTTP --priority 120 \
     --source-address-prefixes "*" --destination-port-ranges 80 \
     --access Allow --protocol Tcp

   az network nsg rule create \
     --resource-group "$RG" --nsg-name modelguard-demo-nsg \
     --name AllowHTTPS --priority 130 \
     --source-address-prefixes "*" --destination-port-ranges 443 \
     --access Allow --protocol Tcp
   ```

   No exception to the GMS rule: 8080 still gets no rule, at any layer.
3. **Install Caddy and point it at the frontend.** Caddy requests and renews
   the certificate itself, no separate certbot step:

   ```bash
   ssh azureuser@<public-ip>

   sudo ufw allow "80/tcp"
   sudo ufw allow "443/tcp"

   sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
   curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt-get update && sudo apt-get install -y caddy

   sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
   __DOMAIN__ {
       reverse_proxy localhost:9002
   }
   EOF
   # Replace __DOMAIN__ with the real (sub)domain before saving, same
   # placeholder-substituted-outside-git pattern as cloud-init.yaml's
   # __GITHUB_CLONE_TOKEN__, though a domain isn't a secret, just per-instance.
   sudo systemctl enable --now caddy
   sudo systemctl restart caddy
   ```

   A template lives at
   [`deploy/azure/Caddyfile.template`](../../deploy/azure/Caddyfile.template).
   Its real TLS state lives in `/var/lib/caddy/` on the VM, never in this repo.
4. Confirm: `curl -sI https://__DOMAIN__` should return `HTTP/2 200`. The
   certificate is requested automatically on Caddy's first attempt to serve
   that domain; `sudo journalctl -u caddy -n 40` shows `certificate obtained
   successfully` once it lands, usually within seconds of steps 1-3 all being
   in place together.

Port 9002 stays open on the bare IP as a fallback path; not closed here.

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
- [`deploy/azure/Caddyfile.template`](../../deploy/azure/Caddyfile.template) -
  the reverse-proxy config for [Add a custom domain and
  HTTPS](#add-a-custom-domain-and-https-optional). Not applied by cloud-init;
  the domain does not exist at provisioning time.
