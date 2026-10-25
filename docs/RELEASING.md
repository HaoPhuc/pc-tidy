# Releasing pc-tidy.exe

pc-tidy runs as administrator and deletes files, so people must be able to trust the file without clicking through warnings. A release is built only by CI, from a `v*` tag:

1. The `exe` job builds pc-tidy.exe in a venv with PyInstaller alone, puts the tag in its Properties, and tests it.
2. Once every job has passed, the `release` job takes that exe and:
   - signs it, once the setup below is done
   - hashes it and tests the very file that is published (`tests/test_exe.py`, which also checks the signature)
   - adds a build provenance attestation
   - publishes a GitHub Release with the exe, its `.sha256` and notes on how to check both

Anyone can then check a download (see the README's *Check what you downloaded*): the SHA-256, `gh attestation verify pc-tidy.exe -R HaoPhuc/pc-tidy`, and the signature in *Properties > Digital Signatures*.

pc-tidy.exe never downloads anything and never updates itself, so nothing can be slipped in after the download.

## Making a release

On `main`, after the pull request is merged:

```powershell
git pull
git tag v2.0.0
git push origin v2.0.0
```

Then:
- Watch the run in the *Actions* tab. If the `release` environment asks for approval, approve it there.
- When it's done, download pc-tidy.exe from the release page on a PC that doesn't have Python.
- Go through the *pc-tidy.exe* part of [CHECKLIST.md](CHECKLIST.md), including the SmartScreen and Defender checks.

Until signing is set up, a release is still published, and its notes say "Signed: no". The run shows a warning, and SmartScreen warns about the file.

## Setting up signing (once)

Signing uses Microsoft's cloud signing service, **Azure Artifact Signing** (called *Trusted Signing* before), through `azure/artifact-signing-action`. It costs a monthly fee (see Azure's pricing page), and Microsoft first checks who you are. This is the only part of the plan that costs money. You do these steps yourself; nothing in the repository holds a password or a key.

1. **In Azure**, with a subscription:
   - Create an **Artifact Signing account**. Its region gives its endpoint, for example `https://eus.codesigning.azure.net/`.
   - Do the **identity validation**, as an individual developer or an organization. The name Microsoft validates is the one people see as the signer.
   - Create a **certificate profile** of type *Public Trust*.
2. **In Microsoft Entra ID**:
   - Create an **app registration** for this repository.
   - Under *Certificates & secrets > Federated credentials*, add one for **GitHub Actions**: organization `HaoPhuc`, repository `pc-tidy`, entity type **Environment**, environment `release`. GitHub then signs in with OpenID Connect, and no client secret exists.
   - On the certificate profile, give that app the **Certificate Profile Signer** role (*Artifact Signing Certificate Profile Signer*, or *Trusted Signing Certificate Profile Signer* before the rename).
3. **In GitHub**, under *Settings > Environments*:
   - Create the environment **`release`**.
   - Under *Deployment branches and tags*, allow only tags that match `v*`. Add yourself as a required reviewer if each release should wait for your approval.
   - In that environment, add these **secrets**:
     - `AZURE_CLIENT_ID`: the app registration's client ID
     - `AZURE_TENANT_ID`
     - `AZURE_SUBSCRIPTION_ID`
   - And these **variables**:
     - `ARTIFACT_SIGNING_ENDPOINT`: the account's endpoint
     - `ARTIFACT_SIGNING_ACCOUNT`: the account's name
     - `ARTIFACT_SIGNING_PROFILE`: the certificate profile's name

The next `v*` tag is signed. `tests/test_exe.py` then also checks that Windows calls the signature *Valid*, and the release notes say "Signed: yes".

A new signer has no SmartScreen reputation yet, so the first downloads of a signed release may still show a warning for a while. The signature also tells people who published the file.
