# Harness2 Operational Cheat Sheet

Panduan ringkas perintah operasional untuk benchmarking dan optimasi agen di **AnD Monitoring System (`dams`)** dan **AD-Strategic-Blueprint (`bp`)**.

---

## 1. Menggunakan Perintah Global CLI (`harness`)

Perintah `harness` sudah terpasang di path eksekutabel sistem Anda. Anda dapat menjalankannya langsung dari terminal PowerShell/CMD di direktori proyek mana pun tanpa perlu `cd` ke `D:\Apps\harness2`:

```powershell
# Menampilkan menu & panduan cepat
harness

# Cek kesiapan adapter
harness check dams
harness check bp

# Preview urutan task (Zero Cost / Tanpa Panggil API)
harness dry dams
harness dry bp

# Menjalankan eksperimen nyata LLM (Propose-Probe-Compose)
harness run dams
harness run bp

# Menjalankan hanya 1 domain tertentu
harness run dams backend         # Khusus backend DAMS
harness run dams architecture    # Khusus arsitektur DAMS
harness run bp wi                # Khusus Work Instructions Blueprint
harness run bp integrity         # Khusus Doc Integrity Blueprint

# Penilaian otomatis hasil deliverable
harness judge dams
harness judge bp

# Menampilkan laporan akhir skor & biaya USD
harness report dams
harness report bp
```

*(Catatan: Anda juga tetap bisa memanggil `.\harness.ps1` jika sedang berada di dalam direktori `D:\Apps\harness2`)*

---

## 2. Perintah Asli Python CLI

Jika Anda ingin menjalankan perintah Python mentah:

| Kebutuhan | Perintah untuk `AnD Monitoring System` (`dams`) | Perintah untuk `AD-Strategic-Blueprint` (`bp`) |
|---|---|---|
| **Cek Kesiapan** | `python -m harness2.cli.check --bench dams` | `python -m harness2.cli.check --bench bp` |
| **Preview Stream (Dry-Run)** | `python -m harness2.cli.run_sweep --bench dams --dry-run` | `python -m harness2.cli.run_sweep --bench bp --dry-run` |
| **Eksperimen Nyata (LLM)** | `python -m harness2.cli.run_sweep --bench dams --k 1 --mode parallel` | `python -m harness2.cli.run_sweep --bench bp --k 1 --mode parallel` |
| **Grading Otomatis** | `python -m harness2.cli.judge --bench dams --k 1 --modes parallel` | `python -m harness2.cli.judge --bench bp --k 1 --modes parallel` |
| **Laporan Hasil** | `python -m harness2.cli.report --bench dams --k 1 --modes parallel` | `python -m harness2.cli.report --bench bp --k 1 --modes parallel` |

---

## 3. Catatan Lokasi File Penting

- **Fixtures Golden Tasks DAMS:** [`dependencies/dams-tasks/`](dependencies/dams-tasks/)
- **Fixtures Golden Tasks Blueprint:** [`dependencies/blueprint-tasks/`](dependencies/blueprint-tasks/)
- **Hasil Eksekusi & Audit Log:** `runs/dams/adapt/` dan `runs/bp/adapt/`
