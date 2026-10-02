# Isolated guest AMI

The EC2 broker expects an x86_64 Ubuntu 24.04 AMI built from this template.
Packer uses a **pinned source AMI ID** in a selected account/region and a
separate build subnet with internet access. The runtime guest is launched in
the isolated subnet defined by `infra/terraform/sandbox-network`; the build
subnet must never be used for tenant execution. No account or source AMI has
been selected, so this template has not produced an AMI.

From the repository root, run `python scripts/build_guest_runtime.py`, then
run `packer init`, `packer validate` and `packer build` in this directory with
`-var region=... -var source_ami=ami-... -var build_subnet_id=subnet-...`.
The generated `guest-runtime.tar` and `manifest.json` are ignored by Git.
The builder installs the locked Python project, Node 24.21.0 verified by its
published SHA-256, and Playwright's Chromium plus Linux dependencies. It runs
an import/browser smoke test before imaging. The guest account is uid 10001;
the root owned runtime is not writable by that account.

An AMI build is only one gate. Record the AMI ID and source image, inspect the
resulting snapshot encryption, run the hostile repository isolation probe in
the production subnet, and verify real S3 ready/go/result transport before
enabling hosted admission.
