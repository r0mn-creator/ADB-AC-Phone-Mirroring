#!/usr/bin/env python3
"""
scrcpy_diag.py — Raw stream diagnostic tool
Run this INSTEAD of server.py to see exactly what bytes scrcpy sends.
It will dump the first few packets in detail so we can fix the parser.

Usage:  python scrcpy_diag.py
"""
import struct, socket, subprocess, time, sys

ADB         = "adb"
SCRCPY_JAR  = "scrcpy-server.jar"
SCRCPY_VER  = "3.1"
SOCKET_NAME = "scrcpy"
PORT_VIDEO   = 27183
PORT_CONTROL = 27184

def adb(*args, timeout=15):
    r = subprocess.run([ADB, *args], capture_output=True, timeout=timeout)
    return r.stdout, r.stderr, r.returncode

def hexdump(data, max_bytes=128):
    data = data[:max_bytes]
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32<=b<127 else '.' for b in chunk)
        lines.append(f'  {i:04x}  {hex_part:<48}  |{ascii_part}|')
    return '\n'.join(lines)

def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise EOFError(f"Socket closed at {len(buf)}/{n}")
        buf.extend(chunk)
    return bytes(buf)

def nal_type_name(t):
    return {1:'P-slice',2:'partition-A',3:'partition-B',4:'partition-C',
            5:'IDR',6:'SEI',7:'SPS',8:'PPS',9:'AUD',
            10:'EOSEQ',11:'EOSTR',12:'FILL'}.get(t, f'type{t}')

def parse_nals(data):
    """Find all Annex-B NAL units in data."""
    nals = []
    i = 0
    while i < len(data) - 3:
        sc3 = data[i]==0 and data[i+1]==0 and data[i+2]==1
        sc4 = i+3 < len(data) and data[i]==0 and data[i+1]==0 and data[i+2]==0 and data[i+3]==1
        if sc3 or sc4:
            start = i + (4 if sc4 else 3)
            # find next start code
            j = start + 1
            while j < len(data) - 2:
                if data[j]==0 and data[j+1]==0 and (data[j+2]==1 or (j+3<len(data) and data[j+2]==0 and data[j+3]==1)):
                    break
                j += 1
            payload = data[start:j] if j < len(data)-2 else data[start:]
            ntype = payload[0] & 0x1f if payload else 0
            nals.append({'type': ntype, 'name': nal_type_name(ntype), 'len': len(payload), 'payload': payload})
            i = j
        else:
            i += 1
    return nals

print("=" * 60)
print("scrcpy v3 raw stream diagnostic")
print("=" * 60)

# 1. Push jar
print("\n[1] Pushing scrcpy-server.jar...")
out, err, rc = adb("push", SCRCPY_JAR, "/data/local/tmp/scrcpy-server.jar", timeout=30)
if rc != 0:
    print(f"FAILED: {err.decode()}")
    sys.exit(1)
print("    OK")

# 2. Kill existing
adb("shell", "pkill", "-f", "scrcpy-server")
time.sleep(0.3)

# 3. Forward ports
print(f"\n[2] Forwarding ports {PORT_VIDEO} and {PORT_CONTROL} → localabstract:{SOCKET_NAME}")
adb("forward", f"tcp:{PORT_VIDEO}",   f"localabstract:{SOCKET_NAME}")
adb("forward", f"tcp:{PORT_CONTROL}", f"localabstract:{SOCKET_NAME}")

# 4. Launch server
cmd = (
    f"CLASSPATH=/data/local/tmp/scrcpy-server.jar "
    f"app_process / com.genymobile.scrcpy.Server "
    f"{SCRCPY_VER} "
    f"tunnel_forward=true "
    f"video=true audio=false control=true "
    f"video_codec=h264 video_bit_rate=2000000 max_fps=10 max_size=0 "
    f"lock_video_orientation=-1 stay_awake=true "
    f"show_touches=false power_off_on_close=false clipboard_autosync=false"
)
print(f"\n[3] Launching scrcpy server on device...")
proc = subprocess.Popen([ADB, "shell", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(1.5)

# 5. Connect
print(f"\n[4] Connecting video socket on :{PORT_VIDEO}...")
vsock = socket.socket()
vsock.settimeout(5)
for attempt in range(10):
    try:
        vsock.connect(("127.0.0.1", PORT_VIDEO))
        print(f"    Connected (attempt {attempt+1})")
        break
    except Exception as e:
        print(f"    Retry {attempt+1}: {e}")
        time.sleep(0.5)
else:
    print("FAILED to connect video socket")
    proc.kill()
    sys.exit(1)

print(f"\n[5] Connecting control socket on :{PORT_CONTROL}...")
csock = socket.socket()
csock.settimeout(5)
for attempt in range(8):
    try:
        csock.connect(("127.0.0.1", PORT_CONTROL))
        print(f"    Connected (attempt {attempt+1})")
        break
    except Exception as e:
        print(f"    Retry {attempt+1}: {e}")
        time.sleep(0.5)
else:
    print("WARNING: control socket failed (non-fatal)")

vsock.settimeout(10)

# 6. Read and dump raw bytes
print("\n[6] Reading raw bytes from video socket...")
print("    (reading first 256 bytes raw, no parsing)\n")

try:
    first_chunk = recv_exact(vsock, 256)
    print(f"First 256 bytes received:\n{hexdump(first_chunk, 256)}\n")
except Exception as e:
    print(f"ERROR reading: {e}")
    proc.kill()
    sys.exit(1)

# 7. Try to interpret as handshake
print("[7] Attempting to interpret as scrcpy handshake...")
print()

# Option A: 1-byte dummy + 76-byte header (our current assumption)
print("--- Option A: 1-byte dummy + 64-byte name + 4-byte codec + 4-byte W + 4-byte H ---")
dummy = first_chunk[0]
print(f"  dummy byte: 0x{dummy:02x} ({dummy})")
name_a = first_chunk[1:65].split(b'\x00')[0]
codec_a = struct.unpack(">I", first_chunk[65:69])[0]
w_a = struct.unpack(">I", first_chunk[69:73])[0]
h_a = struct.unpack(">I", first_chunk[73:77])[0]
print(f"  name:  {name_a}")
print(f"  codec: 0x{codec_a:08x} = '{codec_a.to_bytes(4,'big').decode('ascii','replace')}'")
print(f"  W×H:   {w_a}×{h_a}")
print(f"  {'LOOKS VALID' if 0 < w_a < 10000 and 0 < h_a < 10000 else 'INVALID dimensions'}")
print()

# Option B: No dummy byte — 64-byte name + 4-byte codec + 4-byte W + 4-byte H
print("--- Option B: no dummy — 64-byte name + 4-byte codec + 4-byte W + 4-byte H ---")
name_b = first_chunk[0:64].split(b'\x00')[0]
codec_b = struct.unpack(">I", first_chunk[64:68])[0]
w_b = struct.unpack(">I", first_chunk[68:72])[0]
h_b = struct.unpack(">I", first_chunk[72:76])[0]
print(f"  name:  {name_b}")
print(f"  codec: 0x{codec_b:08x} = '{codec_b.to_bytes(4,'big').decode('ascii','replace')}'")
print(f"  W×H:   {w_b}×{h_b}")
print(f"  {'LOOKS VALID' if 0 < w_b < 10000 and 0 < h_b < 10000 else 'INVALID dimensions'}")
print()

# Option C: stream starts immediately with NAL data (no handshake)
print("--- Option C: starts immediately with packet header (no handshake at all) ---")
pts_c = struct.unpack(">Q", first_chunk[0:8])[0]
sz_c  = struct.unpack(">I", first_chunk[8:12])[0]
print(f"  pts_raw: 0x{pts_c:016x} ({pts_c})")
print(f"  size:    {sz_c}")
print(f"  {'LOOKS VALID' if sz_c < 500000 else 'INVALID size'}")
print()

# 8. Now let's try reading more packets assuming Option A (most likely)
# Find which option gave valid dims and use that offset
offset = None
if 0 < w_a < 10000 and 0 < h_a < 10000:
    print(f"[8] Using Option A offset (77 bytes consumed by handshake)")
    offset = 77
elif 0 < w_b < 10000 and 0 < h_b < 10000:
    print(f"[8] Using Option B offset (76 bytes consumed by handshake)")
    offset = 76
else:
    print(f"[8] Neither A nor B gave valid dims — trying raw packet parse from byte 0")
    offset = 0

# Read the remaining bytes after handshake and next full packets
print(f"\n[9] Reading video packets (offset={offset} into first chunk + more data)...")
print()

# Reconstitute remaining bytes
remaining = bytearray(first_chunk[offset:])

# Read more data to get complete packets
try:
    more = vsock.recv(65536)
    remaining.extend(more)
    more2 = vsock.recv(65536)
    remaining.extend(more2)
except:
    pass

print(f"Have {len(remaining)} bytes of video data after handshake")
print(f"First 32 bytes after handshake:\n{hexdump(bytes(remaining[:32]), 32)}\n")

# Parse packets
print("Parsing as scrcpy packets [8-byte PTS][4-byte size][data]:")
pos = 0
for pkt_num in range(10):
    if pos + 12 > len(remaining):
        print(f"  Not enough data for packet {pkt_num+1} header (pos={pos}, have={len(remaining)})")
        break

    pts_raw = struct.unpack(">Q", remaining[pos:pos+8])[0]
    size    = struct.unpack(">I", remaining[pos+8:pos+12])[0]
    is_cfg  = bool(pts_raw >> 63)
    pts_us  = pts_raw & ~(1 << 63)

    print(f"  Packet {pkt_num+1}: pts_raw=0x{pts_raw:016x} size={size} is_config={is_cfg} pts_us={pts_us}")

    if size == 0 or size > 10_000_000:
        print(f"    *** INVALID size — stream parsing is broken at offset {pos} ***")
        print(f"    Bytes at pos: {hexdump(bytes(remaining[pos:pos+32]), 32)}")
        break

    if pos + 12 + size > len(remaining):
        print(f"    Incomplete packet (need {size} bytes, have {len(remaining)-pos-12})")
        break

    pkt_data = bytes(remaining[pos+12:pos+12+size])
    nals = parse_nals(pkt_data)
    nal_summary = [n['name'] + '(' + str(n['len']) + 'B)' for n in nals]
    print(f"    NALs: {nal_summary}")

    if nals:
        for n in nals:
            if n['type'] in (7, 8) and n['len'] >= 4:
                p = n['payload']
                print(f"      {n['name']} payload[0:8]: {' '.join(f'{b:02x}' for b in p[:8])}")

    pos += 12 + size
    print()

print("\n[DONE] Kill the process and report what you see above.")
print("The 'LOOKS VALID' option tells us the correct handshake format.")
print("The packet list tells us if packet parsing is working.")
print()
proc.kill()
vsock.close()
try: csock.close()
except: pass
