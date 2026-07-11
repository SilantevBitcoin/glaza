"""Детект железа: что за GPU, сколько памяти. Без реального GPU — на моках."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hardware


class ParseNvidiaSmi(unittest.TestCase):
    def test_name_and_vram(self):
        # 8188 MiB = 7.996 GiB -> 8.0: карта «на 8 ГБ» и должна читаться как 8
        out = "NVIDIA GeForce RTX 4070 Laptop GPU, 8188 MiB\n"
        self.assertEqual(hardware._parse_nvidia_smi(out),
                         ("NVIDIA GeForce RTX 4070 Laptop GPU", 8.0))

    def test_vram_is_gib_not_marketing_gb(self):
        self.assertEqual(hardware._parse_nvidia_smi("RTX 3050, 4096 MiB")[1], 4.0)

    def test_garbage_is_not_a_gpu(self):
        self.assertIsNone(hardware._parse_nvidia_smi("bash: nvidia-smi: not found"))

    def test_empty(self):
        self.assertIsNone(hardware._parse_nvidia_smi(""))


class Detect(unittest.TestCase):
    """Отсутствие инструмента — это факт «такого GPU нет», а не ошибка."""

    def _detect(self, system, machine, run=None, gpu_names=()):
        with mock.patch.object(hardware, "_run", return_value=run), \
             mock.patch.object(hardware, "_gpu_names", return_value=list(gpu_names)), \
             mock.patch.object(hardware, "_ram_gb", return_value=16.0), \
             mock.patch.object(hardware.platform, "system", return_value=system), \
             mock.patch.object(hardware.platform, "machine", return_value=machine):
            return hardware.detect()

    def test_no_nvidia_smi_no_gpu(self):
        self.assertEqual(self._detect("Linux", "x86_64").gpu_vendor, "none")

    def test_apple_silicon(self):
        self.assertEqual(self._detect("Darwin", "arm64").gpu_vendor, "apple")

    def test_intel_arc_by_name(self):
        hw = self._detect("Windows", "AMD64", gpu_names=["Intel(R) Arc(TM) A770 Graphics"])
        self.assertEqual(hw.gpu_vendor, "intel_arc")

    def test_amd_by_name(self):
        hw = self._detect("Linux", "x86_64", gpu_names=["AMD Radeon RX 7900 XTX"])
        self.assertEqual(hw.gpu_vendor, "amd")

    def test_nvidia_wins_over_names(self):
        with mock.patch.object(hardware, "_nvidia", return_value=("RTX 4090", 24.0)), \
             mock.patch.object(hardware, "_ram_gb", return_value=64.0), \
             mock.patch.object(hardware.platform, "system", return_value="Windows"), \
             mock.patch.object(hardware.platform, "machine", return_value="AMD64"):
            hw = hardware.detect()
        self.assertEqual((hw.gpu_vendor, hw.vram_gb), ("nvidia", 24.0))


if __name__ == "__main__":
    unittest.main()
