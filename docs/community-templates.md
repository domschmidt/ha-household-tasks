# Community template packs

Household Tasks community packs are small, signed JSON documents. Installation
is always a preview-first operation: the integration downloads and validates a
pack, shows its publisher and requirements, then downloads it again before
installation and compares the digest.

## Security model

- HTTPS on port 443 only; credentials in URLs are rejected.
- Resolved loopback, private, link-local, multicast, and reserved addresses are
  rejected to reduce SSRF risk.
- Redirects are not followed and the response is limited to 256 KiB.
- Every pack uses a detached Ed25519 signature over canonical JSON.
- The signature authenticates a key. On first use, the administrator must
  verify the displayed SHA-256 public-key fingerprint independently.
- Household Tasks pins that fingerprint to the publisher ID. An unexpected key
  change is rejected.
- Required Home Assistant entities are selected locally and validated against
  the declared domains before installation.

## Format

```json
{
  "kind": "household_tasks_template_pack",
  "schema_version": 1,
  "id": "example.appliances",
  "name": "Example appliance routines",
  "version": "1.2.0",
  "publisher": {
    "id": "example.publisher",
    "name": "Example Publisher",
    "public_key": "BASE64_RAW_ED25519_PUBLIC_KEY"
  },
  "templates": [
    {
      "id": "washer_finished",
      "name": "Empty the washing machine",
      "description": "Creates a task when the washing machine finishes.",
      "category": "Appliances",
      "required_entities": [
        {
          "key": "washer_status",
          "name": "Washing-machine status",
          "description": "An entity that changes to off when the cycle ends.",
          "domains": ["binary_sensor", "sensor"]
        }
      ],
      "task": {
        "enabled": true,
        "name": "Empty the washing machine",
        "assignment": { "type": "open" },
        "schedule": {
          "type": "state_trigger",
          "triggers": [
            {
              "entity_id": "{{washer_status}}",
              "to": "off",
              "for": "00:02:00"
            }
          ],
          "due_after": "00:00:00",
          "cooldown": "04:00:00",
          "skip_if_open": true
        }
      }
    }
  ],
  "signature": {
    "algorithm": "ed25519",
    "value": "BASE64_SIGNATURE"
  }
}
```

IDs use lowercase ASCII letters, digits, dots, underscores, and hyphens.
Versions use semantic versioning. A pack contains at most 50 templates and a
template at most 30 entity requirements. A placeholder must occupy the complete
string value, for example `"{{washer_status}}"`; arbitrary template code is not
executed.

## Signing

1. Create the JSON document without the `signature` member.
2. Serialize it as UTF-8 JSON with keys sorted, no insignificant whitespace,
   and non-ASCII characters preserved. In Python this is:

   ```python
   canonical = json.dumps(
       payload,
       sort_keys=True,
       separators=(",", ":"),
       ensure_ascii=False,
   ).encode()
   ```

3. Sign `canonical` with the publisher's Ed25519 private key.
4. Base64-encode the raw 64-byte signature and add the `signature` member.
5. Publish the final JSON at a stable public HTTPS URL.

Keep the private key offline or in a protected release-signing environment.
Changing the public key under an existing publisher ID intentionally blocks
updates. A legitimate key rotation therefore needs a new publisher ID and
clear migration documentation.

## Updates and local ownership

The update check compares semantic versions and reports a newer pack. Installing
an update uses the same signature, fingerprint, requirements, and digest checks
as the first installation. Users can choose **Take control** at any time. This
removes package management metadata but preserves the current task rule.
