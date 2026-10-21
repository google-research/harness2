# Harness2 Operational Cheat Sheet

Panduan ringkas perintah operasional untuk benchmarking dan optimasi agen di **AnD Monitoring System (`dams`)** dan **AD-Strategic-Blueprint (`bp`)**.

---

## 1. Menggunakan Script Helper (`.\harness.ps1`)

Anda tidak perlu menghafal parameter CLI yang panjang. Cukup panggil `.\harness.ps1`:

```powershell
# Menampilkan menu & panduan cepat
.\harness.ps1

# Cek kesiapan adapter
.\harness.ps1 check dams
.\harness.ps1 check bp

# Preview urutan task (Zero Cost / Tanpa Panggil API)
.\harness.ps1 dry dams
.\harness.ps1 dry bp

# Menjalankan eksperimen nyata LLM (Propose-Probe-Compose)
.\harness.ps1 run dams
.\harness.ps1 run bp

# Menjalankan hanya 1 domain tertentu
.\harness.ps1 run dams backend         # Khusus backend DAMS
.\harness.ps1 run dams architecture    # Khusus arsitektur DAMS
.\harness.ps1 run bp wi                # Khusus Work Instructions Blueprint
.\harness.ps1 run bp integrity         # Khusus Doc Integrity Blueprint

# Penilaian otomatis hasil deliverable
.\harness.ps1 judge dams
.\harness.ps1 judge bp

# Menampilkan laporan akhir skor & biaya USD
.\harness.ps1 report dams
.\harness.ps1 report bp
```

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
