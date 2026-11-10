"""이 맥에서 브라우저가 쓰는 GPU·CPU 를 잰다(계획 B, 사람 많을 때 브라우저 부하): N초 동안 프로세스별 GPU 시간(ioreg, sudo 없이)과 CPU(top).

사용: python loadtest/mac_browser.py [초=20] [이름=Brave Browser]  → 한 줄 JSON
"""
import json
import plistlib
import re
import subprocess
import sys


def gpu_ns():
    rows = plistlib.loads(subprocess.run(["ioreg", "-r", "-c", "AGXDeviceUserClient", "-a"], capture_output=True).stdout)
    out = {}
    for row in rows:
        creator = row.get("IOUserClientCreator", "?")
        out[creator] = out.get(creator, 0) + sum(u.get("accumulatedGPUTime", 0) for u in row.get("AppUsage") or [])
    return out


def main(seconds, name):
    before = gpu_ns()
    top = subprocess.run(["top", "-l", "2", "-s", str(seconds), "-stats", "pid,cpu,command", "-n", "400"], capture_output=True, text=True).stdout
    after = gpu_ns()
    gpu = {k.split(",")[-1].strip(): round((after[k] - before.get(k, 0)) / (seconds * 1e9) * 100, 1) for k in after}
    second = top.split("PID")[-1].splitlines()[1:]  # 두 번째 표본이 구간 평균이다
    kinds = {}  # pid → 프로세스 종류(브라우저·렌더러·GPU·유틸리티)
    for line in subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True).stdout.splitlines():
        pid, args = line.strip().split(" ", 1)
        if name in args:
            kind = re.search(r"--type=([a-z-]+)", args)
            kinds[pid] = kind.group(1) if kind else "browser"
    cpu = {}
    for line in second:
        m = re.match(r"\s*(\d+)\s+([\d.]+)\s+(.*)", line)
        if m and m.group(1) in kinds:
            kind = kinds[m.group(1)]
            cpu[kind] = round(cpu.get(kind, 0) + float(m.group(2)), 1)
    return {"seconds": seconds, "gpu_pct": {k: v for k, v in gpu.items() if v >= 0.5},
            "cpu_pct": dict(sorted(cpu.items(), key=lambda kv: -kv[1])[:6]), "cpu_total_pct": round(sum(cpu.values()), 1)}


if __name__ == "__main__":
    print(json.dumps(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20, sys.argv[2] if len(sys.argv) > 2 else "Brave Browser"), ensure_ascii=False))
