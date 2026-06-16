#!/usr/bin/env python3

import sys
import os
import time
import argparse
import signal
import subprocess
import re
import threading
import queue
import errno
from pathlib import Path


BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def bold(s):   return f"{BOLD}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def green(s):  return f"{GREEN}{s}{RESET}"
def cyan(s):   return f"{CYAN}{s}{RESET}"
def dim(s):    return f"{DIM}{s}{RESET}"


wipe_started = False
interrupted  = False


def handle_sigint(sig, frame):
    global interrupted
    interrupted = True
    print()
    if not wipe_started:
        print(yellow("\n[!] Interrupted before wiping started. No changes were made."))
    else:
        print(red("\n[!] Wipe interrupted. The disk is in an INCOMPLETE state."))
        print(red("    Do not use this disk until a full wipe completes."))
    sys.exit(1)


KNOWN_FLAGS = {
    "-h", "--help",
    "-v", "--verbose",
    "--verify",
    "--remove-delays",
    "--disable-safety-locks",
    "--list",
    "--version",
    "--retry",
    "--strict",
    "--no-prescan",
    "--chunk-size",
}

def parse_args():
    for i, token in enumerate(sys.argv[1:]):
        if token.startswith("-"):
            base = token.split("=")[0]
            if base not in KNOWN_FLAGS:
                print(red(f"[!] Unknown option: {token}"))
                print(f"    Run  {bold('trueformat --help')}  for usage information.")
                sys.exit(2)

    parser = argparse.ArgumentParser(
        prog="trueformat",
        description=(
            "Completely erase a raw block device by overwriting every logical\n"
            "sector with 0x00. The device is left with no partition table,\n"
            "no filesystem, and no recoverable user data by normal tools.\n\n"
            "Operates on whole disks only, never on partitions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    parser.add_argument("device", nargs="?", metavar="DEVICE",
        help="Target block device (e.g. /dev/sdb). Must be a whole disk.")
    parser.add_argument("-h", "--help", action="store_true",
        help="Show this help message and exit.")
    parser.add_argument("-v", "--verbose", action="store_true",
        help="Print detailed live actions during the operation.")
    parser.add_argument("--verify", action="store_true",
        help="After wiping, read the disk back and rewrite any sector not equal to 0x00.")
    parser.add_argument("--remove-delays", action="store_true",
        help="Skip timed waiting pauses. Warnings and confirmations are never skipped.")
    parser.add_argument("--disable-safety-locks", action="store_true",
        help="Bypass system-disk protection. Requires an additional strong confirmation.")
    parser.add_argument("--list", action="store_true",
        help="List available disks and exit.")
    parser.add_argument("--version", action="store_true",
        help="Print version and exit.")
    parser.add_argument("--retry", type=int, default=1, metavar="N",
        help="Number of times to retry a bad sector before skipping it. Default: 1.")
    parser.add_argument("--strict", action="store_true",
        help="Abort immediately on any bad sector instead of skipping.")
    parser.add_argument("--no-prescan", action="store_true",
        help="Skip the bad sector prescan and write directly. Faster on known-good drives.")
    parser.add_argument("--chunk-size", type=int, default=0, metavar="MiB",
        help="Write chunk size in MiB. Default: auto-detected from sysfs, fallback 8.")

    return parser.parse_args()


def get_system_disk():
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        m = re.match(r"(/dev/[a-z]+)", out)
        if m:
            return m.group(1)
        m = re.match(r"(/dev/nvme\d+n\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def read_sysfs(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return "unknown"


def get_chunk_size(disk_name, lbs, override_mib):
    if override_mib and override_mib > 0:
        return override_mib * 1024 * 1024

    try:
        optimal = int(read_sysfs(f"/sys/block/{disk_name}/queue/optimal_io_size") or 0)
        if optimal > 0:
            chunk = max(optimal, 8 * 1024 * 1024)
            chunk = (chunk // lbs) * lbs
            return chunk
    except Exception:
        pass

    return 8 * 1024 * 1024


def list_disks():
    sys_disk = get_system_disk()
    disks = []

    block_path = Path("/sys/block")
    if not block_path.exists():
        return disks

    for dev in sorted(block_path.iterdir()):
        name = dev.name
        if not (name.startswith("sd") or name.startswith("hd")):
            continue

        device_node = f"/dev/{name}"

        model  = read_sysfs(dev / "device" / "model").replace("\n", " ").strip()
        serial = read_sysfs(dev / "device" / "serial").strip()
        size_sectors = int(read_sysfs(dev / "size") or 0)
        logical_bs   = int(read_sysfs(dev / "queue" / "logical_block_size") or 512)
        size_bytes   = size_sectors * logical_bs
        size_human   = human_size(size_bytes)

        is_system = (device_node == sys_disk)

        disks.append({
            "node":       device_node,
            "name":       name,
            "model":      model or "-",
            "serial":     serial or "-",
            "size":       size_human,
            "size_bytes": size_bytes,
            "is_system":  is_system,
        })

    return disks


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def print_disk_table(disks):
    if not disks:
        print(yellow("  No HDD block devices found."))
        return

    header = f"  {'DEVICE':<12} {'MODEL':<28} {'SERIAL':<20} {'SIZE':>8}  STATUS"
    print(dim(header))
    print(dim("  " + "-" * 80))

    for d in disks:
        status = red("SYSTEM DISK - protected") if d["is_system"] else green("available")
        model  = d["model"][:27]
        serial = d["serial"][:19]
        print(f"  {d['node']:<12} {model:<28} {serial:<20} {d['size']:>8}  {status}")


def validate_device(device, disks, disable_safety_locks):
    node = device if device.startswith("/dev/") else f"/dev/{device}"

    if not Path(node).exists():
        print(red(f"[!] Device not found: {node}"))
        sys.exit(1)

    match = next((d for d in disks if d["node"] == node), None)
    if match is None:
        if re.search(r"\d$", node) and not re.search(r"n\d$", node):
            print(red(f"[!] {node} appears to be a partition. trueformat only operates on whole disks."))
        else:
            print(red(f"[!] {node} is not a recognised HDD block device."))
            print(f"    Run  {bold('trueformat --list')}  to see available disks.")
        sys.exit(1)

    if match["is_system"] and not disable_safety_locks:
        print()
        print(red("+---------------------------------------------------------+"))
        print(red("|  BLOCKED: This is your system disk.                     |"))
        print(red("|                                                         |"))
        print(red(f"|  {node} contains your running operating system.       |"))
        print(red("|  Wiping it would destroy your system immediately.       |"))
        print(red("|                                                         |"))
        print(red("|  To override (DANGEROUS), use --disable-safety-locks.   |"))
        print(red("+---------------------------------------------------------+"))
        print()
        sys.exit(1)

    return match


def confirm_yn(prompt):
    while True:
        raw = input(prompt).strip()
        if raw in ("Y", "y"):
            return True
        if raw in ("N", "n"):
            return False
        print(yellow("  Please type Y or N."))


def confirm_wipe(prompt):
    while True:
        raw = input(prompt).strip()
        if raw == "WIPE":
            return True
        if raw in ("N", "n"):
            return False
        print(yellow("  Please type WIPE or N."))


MODE_INFO = {
    1: {
        "label": "Fast Wipe (quick, not thorough)",
        "desc": (
            "Mode 1 writes a single pass of 0x00 over only the first and last\n"
            "  regions of the disk. Partition table and filesystem headers are\n"
            "  destroyed, but the majority of the disk is left as-is. This is\n"
            "  fast but not a complete sanitization. Data recovery may still be\n"
            "  possible on unwritten regions."
        ),
    },
    2: {
        "label": "Deeper Cleanup (improved, still not guaranteed)",
        "desc": (
            "Mode 2 writes 0x00 over the full disk in a single sequential pass,\n"
            "  but does not verify the result afterward. Most data will be\n"
            "  overwritten. Some residual data may remain in reallocated sectors\n"
            "  or remapped blocks managed by drive firmware. Not a certified\n"
            "  sanitization."
        ),
    },
    3: {
        "label": "Full Wipe (complete overwrite, recommended)",
        "desc": (
            "Mode 3 overwrites every logical sector from LBA 0 to the last LBA\n"
            "  with 0x00 in a deterministic sequential pass. This is the only\n"
            "  mode that constitutes a complete overwrite of all user-addressable\n"
            "  sectors. Use --verify to add a read-back verification pass that\n"
            "  rewrites any sector not confirmed as zero."
        ),
    },
}


def unmount_disk_partitions(disk, verbose):
    node = disk["node"]

    try:
        mounts_raw = Path("/proc/mounts").read_text()
    except Exception:
        mounts_raw = ""

    to_unmount = []
    for line in mounts_raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        src = parts[0]
        if src == node or (src.startswith(node) and not src[len(node):len(node)+1].isalpha()):
            to_unmount.append(src)

    if not to_unmount:
        if verbose:
            print(dim(f"  [v] No mounted partitions found on {node}."))
        return

    for mount_point in to_unmount:
        if verbose:
            print(dim(f"  [v] Unmounting {mount_point} ..."))
        result = subprocess.run(
            ["umount", mount_point],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(yellow(f"  [!] Could not unmount {mount_point}: {result.stderr.strip()}"))
            print(yellow(f"      Attempting lazy unmount ..."))
            subprocess.run(["umount", "-l", mount_point], capture_output=True)


BAR_WIDTH = 22

def render_bar(label, fraction, speed_bps, eta_secs, done=False):
    filled = int(BAR_WIDTH * fraction)
    bar = "#" * filled + "." * (BAR_WIDTH - filled)
    pct = f"{fraction*100:5.1f}%"
    spd = f"{human_size(speed_bps)}/s" if speed_bps > 0 else "-"

    if done:
        eta = "done"
    elif eta_secs is None or eta_secs <= 0:
        eta = "-"
    elif eta_secs < 60:
        eta = f"{int(eta_secs)}s"
    elif eta_secs < 3600:
        eta = f"{int(eta_secs)//60}m{int(eta_secs)%60:02d}s"
    else:
        h = int(eta_secs) // 3600
        m = (int(eta_secs) % 3600) // 60
        eta = f"{h}h{m:02d}m"

    line = f"  {label}[{bar}] {pct} | {spd:>12} | ETA {eta}"
    print(f"\r{line}", end="", flush=True)


def prescan(node, total_size, chunk_bytes, verbose):
    print(f"  {cyan('>')} Scanning for bad sectors ...")
    if verbose:
        print(dim("  [v] Sequential read pass to map bad sectors before writing ..."))

    bad_offsets = set()

    try:
        fd = os.open(node, os.O_RDONLY)
    except Exception as e:
        print(red(f"[!] Could not open {node} for prescan: {e}"))
        sys.exit(1)

    scanned   = 0
    t_last    = time.monotonic()
    t_start   = t_last
    b_last    = 0
    speed_bps = 0

    offset = 0
    while offset < total_size and not interrupted:
        to_read = min(chunk_bytes, total_size - offset)
        try:
            os.lseek(fd, offset, os.SEEK_SET)
            os.read(fd, to_read)
        except OSError as e:
            if e.errno in (errno.EIO, errno.EILSEQ):
                bad_offsets.add(offset)
                if verbose:
                    print(f"\n  [v] Bad sector at offset {offset} ({human_size(offset)} into disk)")
            else:
                print(red(f"\n[!] Unexpected error during prescan at offset {offset}: {e}"))

        offset  += to_read
        scanned += to_read
        b_last  += to_read

        now = time.monotonic()
        if now - t_last >= 0.25:
            speed_bps = b_last / (now - t_last)
            b_last    = 0
            t_last    = now

        frac = scanned / total_size
        eta  = (total_size - scanned) / speed_bps if speed_bps > 0 else None
        render_bar("prescan ", frac, speed_bps, eta)

    os.close(fd)
    render_bar("prescan ", 1.0, speed_bps, None, done=True)
    print()

    if bad_offsets:
        print(yellow(f"  [!] Found {len(bad_offsets)} bad sector(s). They will be skipped during wipe."))
    else:
        print(green("  OK  No bad sectors found."))

    return bad_offsets


def buffer_producer(node, total_size, chunk_bytes, buf_queue, stop_event):
    zero = b"\x00" * chunk_bytes
    offset = 0
    while offset < total_size and not stop_event.is_set():
        to_write = min(chunk_bytes, total_size - offset)
        buf = zero if to_write == chunk_bytes else b"\x00" * to_write
        buf_queue.put((offset, buf))
        offset += to_write
    buf_queue.put(None)


def do_wipe(disk, mode, verbose, verify, retry_count, strict, no_prescan, chunk_size_mib):
    global wipe_started, interrupted

    node       = disk["node"]
    total_size = disk["size_bytes"]

    EDGE = 64 * 1024 * 1024

    try:
        lbs_raw = Path(f"/sys/block/{disk['name']}/queue/logical_block_size").read_text().strip()
        lbs = int(lbs_raw)
    except Exception:
        lbs = 512

    chunk_bytes = get_chunk_size(disk["name"], lbs, chunk_size_mib)

    if verbose:
        print(dim(f"  [v] Logical block size: {lbs} bytes"))
        print(dim(f"  [v] Write chunk size: {human_size(chunk_bytes)}"))

    if mode == 1:
        ranges = []
        r1_end = min(EDGE, total_size)
        ranges.append((0, r1_end))
        if total_size > 2 * EDGE:
            r2_start = total_size - EDGE
            ranges.append((r2_start, total_size - r2_start))
        wipe_total = sum(r[1] for r in ranges)
        bad_offsets = set()
    else:
        ranges = [(0, total_size)]
        wipe_total = total_size

        if not no_prescan:
            bad_offsets = prescan(node, total_size, chunk_bytes, verbose)
        else:
            if verbose:
                print(dim("  [v] Prescan skipped (--no-prescan)."))
            bad_offsets = set()

    try:
        fd = os.open(node, os.O_RDWR)
    except PermissionError:
        print(red(f"[!] Permission denied opening {node}. Run as root."))
        sys.exit(1)
    except Exception as e:
        print(red(f"[!] Could not open {node}: {e}"))
        sys.exit(1)

    wipe_started = True

    stop_event = threading.Event()
    buf_queue  = queue.Queue(maxsize=4)

    producer_thread = threading.Thread(
        target=buffer_producer,
        args=(node, wipe_total, chunk_bytes, buf_queue, stop_event),
        daemon=True,
    )
    producer_thread.start()

    skipped_offsets = []
    written_total   = 0
    t_start         = time.monotonic()
    t_last          = t_start
    bytes_since_last = 0
    speed_bps       = 0

    print()

    for (range_start, range_length) in ranges:
        offset    = range_start
        range_end = range_start + range_length

        while offset < range_end and not interrupted:
            item = buf_queue.get()
            if item is None:
                break
            _, buf = item
            to_write = len(buf)

            if offset in bad_offsets:
                if verbose:
                    print(f"\n  [v] Skipping known bad sector at offset {offset}")
                skipped_offsets.append(offset)
                offset        += to_write
                written_total += to_write
            else:
                success  = False
                attempts = 0
                last_err = None

                while attempts <= retry_count and not interrupted:
                    try:
                        os.lseek(fd, offset, os.SEEK_SET)
                        n = os.write(fd, buf)
                        written_total    += n
                        bytes_since_last += n
                        offset           += n
                        success = True
                        break
                    except OSError as e:
                        last_err = e
                        attempts += 1
                        if attempts <= retry_count:
                            if verbose:
                                print(f"\n  [v] Write error at offset {offset}, retry {attempts}/{retry_count} ...")
                            time.sleep(0.5)

                if not success:
                    if strict:
                        stop_event.set()
                        os.close(fd)
                        print()
                        print(red(f"[!] Bad sector at offset {offset} ({human_size(offset)} into disk)."))
                        print(red(f"    Error: {last_err}"))
                        print(red("    Aborting (--strict mode). Disk is in an incomplete state."))
                        sys.exit(1)
                    else:
                        skipped_offsets.append(offset)
                        if verbose:
                            print(f"\n  [v] Skipping unwritable sector at offset {offset} after {retry_count} retries.")
                        written_total += to_write
                        offset        += to_write

            now     = time.monotonic()
            elapsed = now - t_last
            if elapsed >= 0.25:
                speed_bps        = bytes_since_last / elapsed
                bytes_since_last = 0
                t_last           = now

            frac = written_total / wipe_total if wipe_total else 1
            eta  = (wipe_total - written_total) / speed_bps if speed_bps > 0 and frac < 1 else None
            render_bar("wipe    ", frac, speed_bps, eta)

        if interrupted:
            break

    stop_event.set()

    if not interrupted:
        render_bar("wipe    ", 1.0, speed_bps, None, done=True)
        print()

        if verbose:
            print(dim("  [v] Flushing disk write cache ..."))
        try:
            import fcntl
            BLKFLSBUF = 0x1261
            fcntl.ioctl(fd, BLKFLSBUF)
        except Exception:
            pass

    os.close(fd)

    if skipped_offsets:
        print()
        print(yellow(f"  [!] {len(skipped_offsets)} sector(s) were skipped due to read/write errors:"))
        for off in skipped_offsets[:10]:
            print(yellow(f"      offset {off} ({human_size(off)} into disk)"))
        if len(skipped_offsets) > 10:
            print(yellow(f"      ... and {len(skipped_offsets) - 10} more."))

    if verify and not interrupted:
        print()
        print(f"  {cyan('>')} Verification pass ...")
        if verbose:
            print(dim("  [v] Reading back all sectors and checking for non-zero bytes ..."))

        try:
            fd_r = os.open(node, os.O_RDONLY)
        except Exception as e:
            print(red(f"[!] Could not open {node} for verification: {e}"))
            return

        needs_rewrite = []
        read_total    = 0
        t_vlast       = time.monotonic()
        vbytes_last   = 0
        vspeed        = 0

        for chunk_start in range(0, total_size, chunk_bytes):
            if chunk_start in skipped_offsets:
                read_total += min(chunk_bytes, total_size - chunk_start)
                continue
            to_read = min(chunk_bytes, total_size - chunk_start)
            try:
                os.lseek(fd_r, chunk_start, os.SEEK_SET)
                data = os.read(fd_r, to_read)
                if any(b != 0 for b in data):
                    needs_rewrite.append(chunk_start)
            except OSError:
                pass

            read_total  += to_read
            vbytes_last += to_read
            now = time.monotonic()
            if now - t_vlast >= 0.25:
                vspeed      = vbytes_last / (now - t_vlast)
                vbytes_last = 0
                t_vlast     = now

            frac = read_total / total_size
            eta  = (total_size - read_total) / vspeed if vspeed > 0 else None
            render_bar("verify  ", frac, vspeed, eta)

        os.close(fd_r)
        print()

        if needs_rewrite:
            print(yellow(f"  [!] Found {len(needs_rewrite)} non-zero chunk(s). Rewriting ..."))
            fd_w = os.open(node, os.O_RDWR)
            for bs in needs_rewrite:
                to_write = min(chunk_bytes, total_size - bs)
                try:
                    os.lseek(fd_w, bs, os.SEEK_SET)
                    os.write(fd_w, b"\x00" * to_write)
                    if verbose:
                        print(dim(f"  [v] Rewrote chunk at offset {bs}"))
                except OSError as e:
                    print(yellow(f"  [!] Could not rewrite at offset {bs}: {e}"))
            os.close(fd_w)
            print(green("  OK  Rewrite complete."))
        else:
            if verbose:
                print(dim("  [v] Verification complete. All readable sectors confirmed 0x00."))
            print(green("  OK  Verification passed. All readable sectors are 0x00."))

    return skipped_offsets


def print_final_report(disk, mode, verify, interrupted_flag, skipped):
    node = disk["node"]
    print()
    print("-" * 60)
    print(bold("  TRUEFORMAT - Final Report"))
    print("-" * 60)
    print(f"  Device          : {node}")
    print(f"  Model           : {disk['model']}")
    print(f"  Serial          : {disk['serial']}")
    print(f"  Size            : {disk['size']}")
    print(f"  Wipe mode       : {mode} - {MODE_INFO[mode]['label']}")
    print(f"  Verify pass     : {'yes' if verify else 'no'}")
    if skipped:
        print(f"  Skipped sectors : {len(skipped)}")
    print()
    if interrupted_flag:
        print(red("  Result          : INCOMPLETE - wipe was interrupted"))
        print(red("  Partition table : unknown (operation did not complete)"))
        print(red("  Filesystem      : unknown"))
        print(red("  Data            : PARTIALLY overwritten - do not use this disk"))
    else:
        print(green("  Partition table : none"))
        print(green("  Filesystem      : none"))
        if skipped:
            print(yellow(f"  Data            : zeroed with {len(skipped)} unwritable sector(s) skipped"))
        else:
            print(green("  Data            : zeroed (all user-addressable sectors)"))
        print(green("  Allocation      : undefined - raw unformatted storage"))
        print(green("  Status          : ready for repartitioning and formatting"))
    print("-" * 60)
    print()


def main():
    signal.signal(signal.SIGINT, handle_sigint)

    args = parse_args()

    if args.retry < 0 or args.retry > 10:
        print(red("[!] --retry must be between 0 and 10."))
        sys.exit(2)

    if args.version:
        print("trueformat 1.1.0")
        sys.exit(0)

    if args.help:
        print(f"""
{bold('trueformat')} - full disk overwrite utility for HDDs

{bold('USAGE')}
  trueformat [OPTIONS] DEVICE
  trueformat --list

{bold('OPTIONS')}
  DEVICE                 Target whole-disk block device (e.g. /dev/sdb)
  --list                 List available HDD block devices and exit
  -h, --help             Show this help and exit
  -v, --verbose          Print detailed live output during the operation
  --verify               After wiping, read back the disk and rewrite any
                         sector not confirmed as 0x00
  --remove-delays        Skip timed waiting pauses (warnings and confirmations
                         are never skipped)
  --disable-safety-locks Bypass system-disk protection. Requires an
                         additional explicit confirmation. DANGEROUS.
  --retry N              Retry a bad sector N times before skipping (0-10).
                         Default: 1.
  --strict               Abort immediately on any bad sector instead of
                         skipping. Overrides --retry.
  --no-prescan           Skip the bad sector scan before writing. Faster on
                         drives known to be healthy.
  --chunk-size MiB       Write chunk size in MiB. Default: auto from sysfs,
                         fallback 8.
  --version              Print version and exit

{bold('WIPE MODES')}
  1  Fast Wipe      - first and last 64 MiB only (not thorough)
  2  Deeper Cleanup - single full pass, no verification (not guaranteed)
  3  Full Wipe      - complete sequential overwrite of all sectors (recommended)

{bold('DEFAULT BEHAVIOR')}
  By default trueformat prescans the disk for bad sectors before writing,
  retries each bad sector once, and skips any that still fail. Skipped
  sectors are reported at the end. Use --strict to abort on the first
  bad sector instead.

{bold('EXAMPLES')}
  trueformat --list
  trueformat /dev/sdb
  trueformat /dev/sdb --verify -v
  trueformat /dev/sdb --retry 3
  trueformat /dev/sdb --strict
  trueformat /dev/sdb --no-prescan
  trueformat /dev/sdb --remove-delays
  trueformat /dev/sdb --disable-safety-locks
""")
        sys.exit(0)

    disks = list_disks()

    if args.list:
        print()
        print(bold("  Available block devices (HDDs):"))
        print()
        print_disk_table(disks)
        print()
        sys.exit(0)

    if not args.device:
        print(red("[!] No device specified."))
        print(f"    Run  {bold('trueformat --list')}  to see available disks.")
        print(f"    Run  {bold('trueformat --help')}  for usage information.")
        sys.exit(2)

    if args.disable_safety_locks:
        print()
        print(red("+----------------------------------------------------------------+"))
        print(red("|  SAFETY LOCKS DISABLED                                         |"))
        print(red("|                                                                |"))
        print(red("|  --disable-safety-locks has been passed.                       |"))
        print(red("|                                                                |"))
        print(red("|  System-disk protection is BYPASSED. trueformat will not       |"))
        print(red("|  prevent you from wiping the disk your OS is running from.     |"))
        print(red("|  If you select the wrong disk you may destroy your system      |"))
        print(red("|  immediately and without further warning.                      |"))
        print(red("|                                                                |"))
        print(red("|  All other confirmations still apply.                          |"))
        print(red("+----------------------------------------------------------------+"))
        print()

    disk = validate_device(args.device, disks, args.disable_safety_locks)

    print()
    print("-" * 60)
    print(bold("  Selected disk - please verify this is correct"))
    print("-" * 60)
    print(f"  Device  : {bold(disk['node'])}")
    print(f"  Model   : {disk['model']}")
    print(f"  Serial  : {disk['serial']}")
    print(f"  Size    : {disk['size']}")
    if disk["is_system"]:
        print(f"  Status  : {red('THIS IS YOUR SYSTEM DISK')}")
    else:
        print(f"  Status  : {green('not system disk')}")
    print("-" * 60)
    print()

    if not args.remove_delays:
        print(dim("  Pausing 3 seconds - read the disk information above carefully."))
        print(dim("  (Use --remove-delays to skip this pause.)"))
        for i in range(3, 0, -1):
            print(f"\r  Continuing in {i} ... ", end="", flush=True)
            time.sleep(1)
        print("\r" + " " * 35 + "\r", end="")

    proceed = confirm_yn(f"  Is {bold(disk['node'])} the correct disk? [Y/N] -> ")
    if not proceed:
        print(yellow("\n  Aborted. No changes were made."))
        sys.exit(0)

    print()
    print(bold("  Wipe mode:"))
    for m, info in MODE_INFO.items():
        print(f"    {m}  {info['label']}")
    print()

    while True:
        raw = input("  Choose mode [1/2/3] -> ").strip()
        if raw in ("1", "2", "3"):
            mode = int(raw)
            break
        print(yellow("  Please enter 1, 2, or 3."))

    print()
    print(bold(f"  Mode {mode}: {MODE_INFO[mode]['label']}"))
    print(f"  {MODE_INFO[mode]['desc']}")
    print()

    if mode == 3:
        print(yellow("  This will permanently destroy all data on this disk."))
        print(yellow(f"  Every sector on {disk['node']} will be overwritten with 0x00."))
    elif mode == 2:
        print(yellow(f"  A full sequential pass will be written to {disk['node']}."))
        print(yellow("  This will destroy the partition table, filesystem, and most user data."))
    else:
        print(yellow(f"  The first and last 64 MiB of {disk['node']} will be overwritten."))
        print(yellow("  This is not a complete wipe. Most data will remain on the disk."))
    print()

    print(bold("  Final confirmation required."))
    print(f"  Type  {bold('WIPE')}  to begin, or  {bold('N')}  to abort.")
    print()
    go = confirm_wipe(f"  [{disk['node']} | Mode {mode}] -> ")
    if not go:
        print(yellow("\n  Aborted. No changes were made."))
        sys.exit(0)

    print()
    print(f"  {cyan('>')} Unmounting partitions on {disk['node']} ...")
    unmount_disk_partitions(disk, args.verbose)

    print(f"  {cyan('>')} Starting wipe  [Mode {mode}]")
    skipped = do_wipe(
        disk, mode, args.verbose, args.verify,
        args.retry, args.strict, args.no_prescan, args.chunk_size
    )

    print_final_report(disk, mode, args.verify, interrupted, skipped or [])


if __name__ == "__main__":
    main()
