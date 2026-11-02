"""roles/data_disks finds each extra disk by the serial lsblk reports. On Proxmox VE a SCSI disk's device ID is
its drive's name (drive-scsi1), and /dev/disk/by-id is named after that, so a by-id link named after the serial
exists on some systems only (AlmaLinux 9, not Ubuntu 26.04). The lsblk answers below are from real VMs on
Proxmox VE 9.2. Run with `make catalog-test`."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("data_disks", ROOT / "roles/data_disks/filter_plugins/data_disks.py")
dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dd)

# lsblk --json --nodeps --output PATH,SERIAL, on Ubuntu 26.04 (util-linux 2.41) and AlmaLinux 9 (2.37) alike.
PROXMOX = """{
   "blockdevices": [
      {
         "path": "/dev/sda",
         "serial": "drive-scsi0"
      },{
         "path": "/dev/sdb",
         "serial": "usas-longhorn"
      }
   ]
}"""
LONGHORN = {"label": "longhorn", "mount": "/var/lib/longhorn", "fs": "ext4"}


def found(lsblk, disks):
    results = dd.data_disks_find(lsblk, disks)["results"]
    return [(r["item"]["label"], r["stat"]["exists"], r["stat"]["path"]) for r in results]


def test_a_proxmox_disk_is_found_by_its_serial():
    assert found(PROXMOX, [LONGHORN]) == [("longhorn", True, "/dev/sdb")]


def test_each_disk_in_order_and_a_missing_one_is_named():
    lsblk = ('{"blockdevices": [{"path": "/dev/sdc", "serial": "usas-logs"},'
             ' {"path": "/dev/sdb", "serial": "usas-data"}]}')
    disks = [{"label": "data"}, {"label": "gone"}, {"label": "logs"}]
    assert found(lsblk, disks) == [("data", True, "/dev/sdb"), ("gone", False, ""), ("logs", True, "/dev/sdc")]


def test_the_drive_name_is_not_a_serial_match():
    assert found(PROXMOX, [{"label": "scsi0"}, {"label": "drive-scsi0"}]) == [("scsi0", False, ""),
                                                                               ("drive-scsi0", False, "")]


def test_a_serial_two_disks_share_finds_neither():
    lsblk = ('{"blockdevices": [{"path": "/dev/sdb", "serial": "usas-data"},'
             ' {"path": "/dev/sdc", "serial": "usas-data"}, {"path": "/dev/sdd", "serial": "usas-data"}]}')
    assert found(lsblk, [{"label": "data"}]) == [("data", False, "")]


def test_output_that_cant_be_read_finds_nothing():
    for lsblk in ["", "not json", "[]", '{"blockdevices": null}', '{"blockdevices": [null, {"serial": "usas-data"}]}',
                  None]:
        assert found(lsblk, [{"label": "data"}]) == [("data", False, "")]


def test_the_result_feeds_the_blkid_probe():
    result = dd.data_disks_find(PROXMOX, [LONGHORN])["results"][0]
    probe = {"item": result, "rc": 2, "stdout": ""}
    assert dd.data_disks_state([probe], [LONGHORN])[0]["device"] == "/dev/sdb"
