#!/usr/bin/python
#
# Copyright 2011 Google Inc. All Rights Reserved.

"""Script to image a ChromeOS device.

This script images a remote ChromeOS device with a specific image."
"""

__author__ = "asharif@google.com (Ahmad Sharif)"

import filecmp
import glob
import optparse
import os
import re
import shutil
import sys
import tempfile
import time

from utils import command_executer
from utils import logger
from utils import misc
from utils.file_utils import FileUtils

checksum_file = "/usr/local/osimage_checksum_file"
lock_file = "/tmp/image_chromeos_lock/image_chromeos_lock"

def Usage(parser, message):
  print "ERROR: " + message
  parser.print_help()
  sys.exit(0)


def CheckForCrosFlash(chromeos_root, remote, log_level):
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)

  chroot_has_cros_flash = False
  remote_has_cherrypy = False

  # Check to see if chroot contains cros flash.
  cros_flash_path = os.path.join(os.path.realpath(chromeos_root),
                                 "chromite/cros/commands/cros_flash.py")

  if os.path.exists(cros_flash_path):
    chroot_has_cros_flash = True

  # Check to see if remote machine has cherrypy.
  keypath = os.path.join (os.path.realpath(chromeos_root),
                          "src/scripts/mod_for_test_scripts/ssh_keys/"
                          "testing_rsa")

  command = ("ssh -i %s -o StrictHostKeyChecking=no -o CheckHostIP=no "
             "-o BatchMode=yes -o UserKnownHostsFile=/dev/null "
             "root@%s \"python -c 'import cherrypy'\" " %
             (keypath,remote) )
  retval = cmd_executer.RunCommand (command)
  logger.GetLogger().LogFatalIf(retval == 255, "Failed ssh to %s" % remote)
  if retval == 0:
    remote_has_cherrypy = True

  return (chroot_has_cros_flash and remote_has_cherrypy)

def DoImage(argv):
  """Build ChromeOS."""

  parser = optparse.OptionParser()
  parser.add_option("-c", "--chromeos_root", dest="chromeos_root",
                    help="Target directory for ChromeOS installation.")
  parser.add_option("-r", "--remote", dest="remote",
                    help="Target device.")
  parser.add_option("-i", "--image", dest="image",
                    help="Image binary file.")
  parser.add_option("-b", "--board", dest="board",
                    help="Target board override.")
  parser.add_option("-f", "--force", dest="force",
                    action="store_true",
                    default=False,
                    help="Force an image even if it is non-test.")
  parser.add_option("-l", "--logging_level", dest="log_level",
                    default="verbose",
                    help="Amount of logging to be used. Valid levels are "
                    "'quiet', 'average', and 'verbose'.")
  parser.add_option("-a",
                    "--image_args",
                    dest="image_args")


  options = parser.parse_args(argv[1:])[0]

  if not options.log_level in command_executer.LOG_LEVEL:
    Usage(parser, "--logging_level must be 'quiet', 'average' or 'verbose'")
  else:
    log_level = options.log_level

  # Common initializations
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)
  l = logger.GetLogger()

  if options.chromeos_root is None:
    Usage(parser, "--chromeos_root must be set")

  if options.remote is None:
    Usage(parser, "--remote must be set")

  options.chromeos_root = os.path.expanduser(options.chromeos_root)

  if options.board is None:
    board = cmd_executer.CrosLearnBoard(options.chromeos_root, options.remote)
  else:
    board = options.board

  if options.image is None:
    images_dir = misc.GetImageDir(options.chromeos_root, board)
    image = os.path.join(images_dir,
                         "latest",
                         "chromiumos_test_image.bin")
    if not os.path.exists(image):
      image = os.path.join(images_dir,
                           "latest",
                           "chromiumos_image.bin")
  else:
    image = options.image
    if image.find("xbuddy://") < 0:
      image = os.path.expanduser(image)

  if image.find("xbuddy://") < 0:
    image = os.path.realpath(image)

  if not os.path.exists(image) and image.find("xbuddy://") < 0:
    Usage(parser, "Image file: " + image + " does not exist!")

  reimage = False
  local_image = False
  if image.find("xbuddy://") < 0:
    local_image = True
    image_checksum = FileUtils().Md5File(image, log_level=log_level)

    command = "cat " + checksum_file
    retval, device_checksum, err = cmd_executer.CrosRunCommand(command,
                                         return_output=True,
                                         chromeos_root=options.chromeos_root,
                                         machine=options.remote)

    device_checksum = device_checksum.strip()
    image_checksum = str(image_checksum)

    l.LogOutput("Image checksum: " + image_checksum)
    l.LogOutput("Device checksum: " + device_checksum)

    if image_checksum != device_checksum:
      [found, located_image] = LocateOrCopyImage(options.chromeos_root,
                                                 image,
                                                 board=board)

      reimage = True
      l.LogOutput("Checksums do not match. Re-imaging...")

      is_test_image = IsImageModdedForTest(options.chromeos_root,
                                           located_image, log_level)

      if not is_test_image and not options.force:
        logger.GetLogger().LogFatal("Have to pass --force to image a non-test "
                                    "image!")
  else:
    reimage = True
    found = True
    l.LogOutput("Using non-local image; Re-imaging...")


  if reimage:
    # If the device has /tmp mounted as noexec, image_to_live.sh can fail.
    command = "mount -o remount,rw,exec /tmp"
    cmd_executer.CrosRunCommand(command,
                                chromeos_root=options.chromeos_root,
                                machine=options.remote)

    real_src_dir = os.path.join(os.path.realpath(options.chromeos_root),
                                "src")
    real_chroot_dir = os.path.join(os.path.realpath(options.chromeos_root),
                                   "chroot")
    if local_image:
      if located_image.find(real_src_dir) != 0:
        if located_image.find(real_chroot_dir) != 0:
          raise Exception("Located image: %s not in chromeos_root: %s" %
                          (located_image, options.chromeos_root))
        else:
          chroot_image = located_image[len(real_chroot_dir):]
      else:
        chroot_image = os.path.join(
            "..",
            located_image[len(real_src_dir):].lstrip("/"))

    # Check to see if cros flash is in the chroot or not.
    use_cros_flash = CheckForCrosFlash (options.chromeos_root,
                                        options.remote, log_level)

    if use_cros_flash:
      # Use 'cros flash'
      if local_image:
        cros_flash_args = ["--board=%s" % board,
                           "--clobber-stateful",
                           options.remote,
                           chroot_image]
      else:

        cros_flash_args = ["--board=%s" % board,
                           "--clobber-stateful",
                           options.remote,
                           image]

      command = ("cros flash %s" % " ".join(cros_flash_args))
    elif local_image:
      # Use 'cros_image_to_target.py'
      cros_image_to_target_args = ["--remote=%s" % options.remote,
                                   "--board=%s" % board,
                                   "--from=%s" % os.path.dirname(chroot_image),
                                   "--image-name=%s" %
                                   os.path.basename(located_image)]

      command = ("./bin/cros_image_to_target.py %s" %
                 " ".join(cros_image_to_target_args))
      if options.image_args:
        command += " %s" % options.image_args
    else:
      raise Exception("Unable to find 'cros flash' in chroot; cannot use "
                      "non-local image (%s) with cros_image_to_target.py" %
                      image)

    # Workaround for crosbug.com/35684.
    os.chmod(misc.GetChromeOSKeyFile(options.chromeos_root), 0600)
    if log_level == "quiet":
      l.LogOutput("CMD : %s" % command)
    elif log_level == "average":
      cmd_executer.SetLogLevel("verbose");
    retval = cmd_executer.ChrootRunCommand(options.chromeos_root,
                                           command, command_timeout=1800)

    retries = 0
    while retval != 0 and retries < 2:
      retries += 1
      if log_level == "quiet":
        l.LogOutput("Imaging failed. Retry # %d." % retries)
        l.LogOutput("CMD : %s" % command)
      retval = cmd_executer.ChrootRunCommand(options.chromeos_root,
                                             command, command_timeout=1800)

    if log_level == "average":
      cmd_executer.SetLogLevel(log_level)

    if found == False:
      temp_dir = os.path.dirname(located_image)
      l.LogOutput("Deleting temp image dir: %s" % temp_dir)
      shutil.rmtree(temp_dir)

    logger.GetLogger().LogFatalIf(retval, "Image command failed")

    # Unfortunately cros_image_to_target.py sometimes returns early when the
    # machine isn't fully up yet.
    retval = EnsureMachineUp(options.chromeos_root, options.remote,
                             log_level)

    # If this is a non-local image, then the retval returned from
    # EnsureMachineUp is the one that will be returned by this function;
    # in that case, make sure the value in 'retval' is appropriate.
    if not local_image and retval == True:
      retval = 0
    else:
      retval = 1

    if local_image:
      if log_level == "average":
        l.LogOutput("Verifying image.")
      command = "echo %s > %s && chmod -w %s" % (image_checksum,
                                                 checksum_file,
                                                 checksum_file)
      retval = cmd_executer.CrosRunCommand(command,
                                          chromeos_root=options.chromeos_root,
                                          machine=options.remote)
      logger.GetLogger().LogFatalIf(retval, "Writing checksum failed.")

      successfully_imaged = VerifyChromeChecksum(options.chromeos_root,
                                                 image,
                                                 options.remote, log_level)
      logger.GetLogger().LogFatalIf(not successfully_imaged,
                                    "Image verification failed!")
      TryRemountPartitionAsRW(options.chromeos_root, options.remote,
                              log_level)
  else:
    l.LogOutput("Checksums match. Skipping reimage")
  return retval


def LocateOrCopyImage(chromeos_root, image, board=None):
  l = logger.GetLogger()
  if board is None:
    board_glob = "*"
  else:
    board_glob = board

  chromeos_root_realpath = os.path.realpath(chromeos_root)
  image = os.path.realpath(image)

  if image.startswith("%s/" % chromeos_root_realpath):
    return [True, image]

  # First search within the existing build dirs for any matching files.
  images_glob = ("%s/src/build/images/%s/*/*.bin" %
                 (chromeos_root_realpath,
                  board_glob))
  images_list = glob.glob(images_glob)
  for potential_image in images_list:
    if filecmp.cmp(potential_image, image):
      l.LogOutput("Found matching image %s in chromeos_root." % potential_image)
      return [True, potential_image]
  # We did not find an image. Copy it in the src dir and return the copied
  # file.
  if board is None:
    board = ""
  base_dir = ("%s/src/build/images/%s" %
              (chromeos_root_realpath,
               board))
  if not os.path.isdir(base_dir):
    os.makedirs(base_dir)
  temp_dir = tempfile.mkdtemp(prefix="%s/tmp" % base_dir)
  new_image = "%s/%s" % (temp_dir, os.path.basename(image))
  l.LogOutput("No matching image found. Copying %s to %s" %
              (image, new_image))
  shutil.copyfile(image, new_image)
  return [False, new_image]


def GetImageMountCommand(chromeos_root, image, rootfs_mp, stateful_mp):
  image_dir = os.path.dirname(image)
  image_file = os.path.basename(image)
  mount_command = ("cd %s/src/scripts &&"
                   "./mount_gpt_image.sh --from=%s --image=%s"
                   " --safe --read_only"
                   " --rootfs_mountpt=%s"
                   " --stateful_mountpt=%s" %
                   (chromeos_root, image_dir, image_file, rootfs_mp,
                    stateful_mp))
  return mount_command


def MountImage(chromeos_root, image, rootfs_mp, stateful_mp, log_level,
               unmount=False):
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)
  command = GetImageMountCommand(chromeos_root, image, rootfs_mp, stateful_mp)
  if unmount:
    command = "%s --unmount" % command
  retval = cmd_executer.RunCommand(command)
  logger.GetLogger().LogFatalIf(retval, "Mount/unmount command failed!")
  return retval


def IsImageModdedForTest(chromeos_root, image, log_level):
  if log_level != "verbose":
    log_level = "quiet"
  rootfs_mp = tempfile.mkdtemp()
  stateful_mp = tempfile.mkdtemp()
  MountImage(chromeos_root, image, rootfs_mp, stateful_mp, log_level)
  lsb_release_file = os.path.join(rootfs_mp, "etc/lsb-release")
  lsb_release_contents = open(lsb_release_file).read()
  is_test_image = re.search("test", lsb_release_contents, re.IGNORECASE)
  MountImage(chromeos_root, image, rootfs_mp, stateful_mp, log_level,
             unmount=True)
  return is_test_image


def VerifyChromeChecksum(chromeos_root, image, remote, log_level):
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)
  rootfs_mp = tempfile.mkdtemp()
  stateful_mp = tempfile.mkdtemp()
  MountImage(chromeos_root, image, rootfs_mp, stateful_mp, log_level)
  image_chrome_checksum = FileUtils().Md5File("%s/opt/google/chrome/chrome" %
                                              rootfs_mp,
                                              log_level=log_level)
  MountImage(chromeos_root, image, rootfs_mp, stateful_mp, log_level,
             unmount=True)

  command = "md5sum /opt/google/chrome/chrome"
  [r, o, e] = cmd_executer.CrosRunCommand(command,
                                          return_output=True,
                                          chromeos_root=chromeos_root,
                                          machine=remote)
  device_chrome_checksum = o.split()[0]
  if image_chrome_checksum.strip() == device_chrome_checksum.strip():
    return True
  else:
    return False

# Remount partition as writable.
# TODO: auto-detect if an image is built using --noenable_rootfs_verification.
def TryRemountPartitionAsRW(chromeos_root, remote, log_level):
  l = logger.GetLogger()
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)
  command = "sudo mount -o remount,rw /"
  retval = cmd_executer.CrosRunCommand(\
    command, chromeos_root=chromeos_root, machine=remote, terminated_timeout=10)
  if retval:
    ## Safely ignore.
    l.LogWarning("Failed to remount partition as rw, "
                 "probably the image was not built with "
                 "\"--noenable_rootfs_verification\", "
                 "you can safely ignore this.")
  else:
    l.LogOutput("Re-mounted partition as writable.")


def EnsureMachineUp(chromeos_root, remote, log_level):
  l = logger.GetLogger()
  cmd_executer = command_executer.GetCommandExecuter(log_level=log_level)
  timeout = 600
  magic = "abcdefghijklmnopqrstuvwxyz"
  command = "echo %s" % magic
  start_time = time.time()
  while True:
    current_time = time.time()
    if current_time - start_time > timeout:
      l.LogError("Timeout of %ss reached. Machine still not up. Aborting." %
                 timeout)
      return False
    retval = cmd_executer.CrosRunCommand(command,
                                         chromeos_root=chromeos_root,
                                         machine=remote)
    if not retval:
      return True


def Main(argv):
  misc.AcquireLock(lock_file)
  try:
    return DoImage(argv)
  finally:
    misc.ReleaseLock(lock_file)


if __name__ == "__main__":
  retval = Main(sys.argv)
  sys.exit(retval)
