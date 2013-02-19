#!/usr/bin/python2.6
#
# Copyright 2010 Google Inc. All Rights Reserved.

"""Script adapter used by automation client for testing dejagnu.
   This is not intended to be run on command line.
   To kick off a single dejagnu run, use chromeos/v14/dejagnu/run_dejagnu.py
"""

__author__ = 'shenhan@google.com (Han Shen)'

import optparse
import os
from os import path
import sys
import setup_chromeos
import build_tc

from dejagnu import run_dejagnu
from utils import command_executer
from utils import email_sender

class DejagnuAdapter(object):

  # TODO(shenhan): move these to constants.py.
  _CHROMIUM_GCC_GIT="http://git.chromium.org/chromiumos/third_party/gcc.git"
  _CHROMIUM_GCC_BRANCH="gcc.gnu.org/branches/google/gcc-4_7-mobile"

  _cmd_exec = command_executer.GetCommandExecuter()

  def __init__(self, board, remote, gcc_dir, chromeos_root):
    self._board = board
    self._remote = remote
    self._gcc_dir = gcc_dir
    self._chromeos_root = chromeos_root

  def SetupChromeOS(self):
    cmd = [setup_chromeos.__file__,
           "--dir=" + self._chromeos_root, "--minilayout", "--jobs=8"]
    ret = setup_chromeos.Main(cmd)
    if ret:
      raise Exception("Failed to checkout chromeos")
    ## Do cros_sdk and setup_board, otherwise build_tc in next step will fail.
    cmd = "cd {0} && cros_sdk --download".format(self._chromeos_root)
    ret = self._cmd_exec.RunCommand(cmd, terminated_timeout=9000)
    if ret:
      raise Exception("Failed to create chroot.")

  def SetupBoard(self):
    cmd = "./setup_board --board=" + self._board
    ret = self._cmd_exec.ChrootRunCommand(self._chromeos_root,
                                          cmd, terminated_timeout=4000)
    if ret:
      raise Exception("Failed to setup board.")

  def CheckoutGCC(self):
    cmd = "git clone {0} {1} && cd {1} && git checkout {2}".format(
      self._CHROMIUM_GCC_GIT, self._gcc_dir, self._CHROMIUM_GCC_BRANCH)

    ret = self._cmd_exec.RunCommand(cmd, terminated_timeout=300)
    if ret:
      raise Exception("Failed to checkout gcc.")
    ## Handle build_tc bug.
    cmd = ("touch {0}/gcc/config/arm/arm-tune.md " + \
        "{0}/gcc/config/arm/arm-tables.opt").format(self._gcc_dir)
    ret = self._cmd_exec.RunCommand(cmd)

  def BuildGCC(self):
    build_gcc_args = [build_tc.__file__,
                      "--board=" + self._board,
                      "--chromeos_root=" + self._chromeos_root,
                      "--gcc_dir=" + self._gcc_dir]
    ret = build_tc.Main(build_gcc_args)
    if ret:
      raise Exception("Building gcc failed.")

  def CheckGCC(self):
    args = [run_dejagnu.__file__,
            "--board=" + self._board,
            "--chromeos_root=" + self._chromeos_root,
            "--mount=" + self._gcc_dir,
            "--remote=" + self._remote]
    return run_dejagnu.Main(args)


def EmailResult(result):
  email_to = ('shenhan@google.com')
  email_from = ('shenhan@google.com')
  if len(result) == 4:
    subject = 'Job failed: dejagnu test didn\'t finish'
    email_text = 'Job failed prematurely, check exception below.\n' + \
        result[3]
  elif result[0]:
    subject = 'Job finished: dejagnu test failed'
    email_text = ('At least 1 new fail found. Please check log below.\n'
                  '\nStdout ====\n'
                  '{0}\n'
                  '\nStderr ===\n'
                  '{1}\n').format(result[1], result[2])
  else:
    subject = 'Job finished: dejagnu test passed'
    email_text = ('Cool! No new fail found.\n'
                  '\nStdout ====\n'
                  '{0}\n'
                  '\nStderr ====\n'
                  '{1}\n').format(result[1], result[2])
    msg_type = 'plain'

  try:
    email_sender.EmailSender().SendEmail(email_to, subject, email_text)
    print 'Email sent.'
  except Exception as e:
    # Do not propagate this email sending exception, you want to email an
    # email exception? Just log it on console.
    print ('Sending email failed - {0}'
           'Subject: {1}'
           'Text: {2}').format(
      str(e), subject, email_text)


def ProcessArguments(argv):
  """Processing script arguments."""
  parser = optparse.OptionParser(description=(
      'This script is used by nightly client to test gcc. '
      'DO NOT run it unless you know what you are doing.'),
      usage='test_gcc_dejagnu.py options')
  parser.add_option('-b', '--board', dest='board',
                    help=('Required. Specify board type. For example '
                          '\'lumpy\' and \'daisy\''))
  parser.add_option('-r', '--remote', dest='remote',
                    help=('Required. Specify remote board address'))
  parser.add_option('-g', '--gcc_dir', dest='gcc_dir', default='gcc.live',
                    help=('Optional. Specify gcc checkout directory.'))
  parser.add_option('-c', '--chromeos_root', dest='chromeos_root',
                    default='chromeos.live',
                    help=('Optional. Specify chromeos checkout directory.'))

  options, args = parser.parse_args(argv)

  if not options.board or not options.remote:
    raise Exception("--board and --remote are mandatory options.")

  return options

def Main(argv):
  opt = ProcessArguments(argv)
  try:
    adapter = DejagnuAdapter(
      opt.board, opt.remote, opt.gcc_dir, opt.chromeos_root)
    adapter.SetupChromeOS()
    adapter.SetupBoard()
    adapter.CheckoutGCC()
    adapter.BuildGCC()
    ret = adapter.CheckGCC()
  except Exception as e:
    print e
    ret = (1, '', '', str(e))
  finally:
    EmailResult(ret)
    return ret


if __name__ == "__main__":
  retval = Main(sys.argv)
  sys.exit(retval[0])
