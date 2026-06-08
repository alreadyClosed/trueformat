## ⚠️ TOOL IS STILL IN TESTING PHASE

# trueformat

**trueformat** is a Linux terminal utility for HDDs that completely erases a
single selected raw block device by overwriting every logical sector with
`0x00`, leaving the drive in a blank, reusable state with no partition table,
no filesystem, and no recoverable user data by normal tools.

> **Operates on whole disks only, never on partitions.**  
> Must be run as root.

---

## Install

```bash
git clone https://github.com/alreadyClosed/trueformat.git
cd trueformat
sudo make install
```

Or with the convenience script:

```
sudo bash install.sh
```

To uninstall:

```
sudo make uninstall
```

---

## Usage

```
trueformat [OPTIONS] DEVICE
trueformat --list
```

### Options

| Flag | Description |
|------|-------------|
| `DEVICE` | Target whole-disk block device (e.g. `/dev/sdb`) |
| `--list` | List available HDD block devices and exit |
| `-h`, `--help` | Show help and exit |
| `-v`, `--verbose` | Print detailed live output during the operation |
| `--verify` | After wiping, read back every sector and rewrite any that are not `0x00` |
| `--remove-delays` | Skip timed pauses (warnings and confirmations are never skipped) |
| `--disable-safety-locks` | Bypass system-disk protection. **Dangerous.** |
| `--version` | Print version and exit |

---

## Wipe modes

| Mode | Label | Description |
|------|-------|-------------|
| `1` | Fast Wipe | Overwrites only the first and last 64 MiB. Not thorough. |
| `2` | Deeper Cleanup | Single full sequential pass. Not verified. |
| `3` | Full Wipe | Overwrites every logical sector 0-last LBA. **Recommended.** |

Only mode 3 constitutes a complete sanitization of all user-addressable sectors.

---

## Workflow

```
trueformat --list
```
```
  DEVICE       MODEL                        SERIAL               SIZE     STATUS
  ────────────────────────────────────────────────────────────────────────────────
  /dev/sda     Samsung SSD 870              S5XXNXXXXX          500.1 GB  SYSTEM DISK — protected
  /dev/sdb     WDC WD10EZEX-08WN4A0         WD-WXXX1234567     931.5 GB  available
```

```
trueformat /dev/sdb
```

1. Disk identity is displayed (model, serial, size).
2. A 3-second pause lets you read the information (skip with `--remove-delays`).
3. First confirmation: **Y / N** — is this the correct disk?
4. Mode selection: **1**, **2**, or **3**.
5. Mode-specific warning is printed.
6. Final confirmation: type **WIPE** or **N**.
7. Partitions on the selected disk are unmounted.
8. All sectors are overwritten with `0x00`, with a live progress bar.
9. Optional verification pass (`--verify`).
10. Final report printed.
    
---

## Safety

- The **system disk** (the disk your OS is running from) is **blocked by default**.  
  Use `--disable-safety-locks` to override, which triggers a much stronger warning
  and requires the same confirmations.
- Interrupting before wiping starts reports: *no changes were made*.
- Interrupting during a wipe reports: *operation incomplete, do not use disk*.

---

## Requirements

- Linux
- Python 3.6+
- Root privileges (`sudo`)
- `findmnt`, `umount` (standard on all Linux distributions)

---

## License

MIT
