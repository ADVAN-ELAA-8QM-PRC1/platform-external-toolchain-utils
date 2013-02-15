#!/bin/bash

# TODO(asharif) use locks here to prevent two processes from doing this
# simultaneously.
toolchain_setup_env()
{
  if [[ -z $FLAGS_TC_ROOT ]]
  then
    return 0
  fi
  GVSB=$FLAGS_TC_ROOT/google_vendor_src_branch
  full_dir=$(dirname $(readlink -e $0))
  parent_dir="${full_dir##*/}"
  # TC_DIRS should contain the full path to the directories you wish to mount.
  # The following code will extract out the last dir and use that mount point
  # inside the chroot.
  TC_DIRS=( $GVSB/gcc ${full_dir} )
  for TC_DIR in ${TC_DIRS[@]}
  do
    TC_LAST_DIR=${TC_DIR##*/}
    TC_MOUNTED_PATH="$(eval readlink -f "$CROS_CHROOT/$FLAGS_TC_ROOT_MOUNT/$TC_LAST_DIR")"
    if [[ -z "$(mount | grep -F "on $TC_MOUNTED_PATH ")" ]]
    then
      ! TC_PATH="$(eval readlink -e "$TC_DIR")"
      TC_MOUNTED_PATH="$CROS_CHROOT/$FLAGS_TC_ROOT_MOUNT/$TC_LAST_DIR"
      if [[ -z "$TC_PATH" ]]
      then
        die "$TC_DIR is not a valid toolchain directory."
      fi
      if [[ -z "$TC_MOUNTED_PATH" ]]
      then
        die "$CROS_CHROOT/$FLAGS_TC_ROOT_MOUNT/$TC_LAST_DIR is not a\
        valid mount directory."
      fi
      info "Mounting $TC_PATH at $TC_MOUNTED_PATH."
      mkdir -p $TC_MOUNTED_PATH
      sudo mount --bind "$TC_PATH" "$TC_MOUNTED_PATH" || \
        die "Could not mount $TC_PATH at $TC_MOUNTED_PATH"
    fi
  done
  # Setup symlinks to build-gcc and build-binutils.
  ln -fs -t $CROS_CHROOT/$FLAGS_TC_ROOT_MOUNT $parent_dir/build-gcc
}

FLAGS_TC_ROOT=""
FLAGS_TC_ROOT_MOUNT="/home/$USER/toolchain_root"
FLAGS_CHROMEOS_DIR=""

# Parse command line arguments.
# TODO(asharif): Make this into a nice loop.
for arg in "$@"
do
  PARSED=0
  key="`echo "$arg" | cut -d '=' -f 1`"
  value="`echo "$arg" | cut -d '=' -f 2`"

  if [[ "$key" == "--toolchain_root" ]]
  then
    let PARSED++
    FLAGS_TC_ROOT=$value
  fi

  if [[ "$key" == "--chromeos_root" ]]
  then
    FLAGS_CHROMEOS_DIR=$value
    let PARSED++
  fi

  if [[ $PARSED -eq 0 ]]
  then
    newargs+="$arg "
  fi
done

if [[ -z $FLAGS_CHROMEOS_DIR ]]
then
  cd ../../ > /dev/null
  FLAGS_CHROMEOS_DIR=$(pwd)
  cd - > /dev/null
fi

eval FLAGS_CHROMEOS_DIR=$FLAGS_CHROMEOS_DIR

if [[ ! -f $FLAGS_CHROMEOS_DIR/src/scripts/common.sh ]]
then
  echo "FATAL: Invalid ChromeOS dir. Please specify the checkout dir by passing:"
  echo "--chromeos_root="
  exit 1
fi

# Enter the scripts directory.

# Source the ChromeOS common.sh script.
. $FLAGS_CHROMEOS_DIR/src/scripts/common.sh

CROS_CHROOT=$DEFAULT_CHROOT_DIR

assert_outside_chroot
assert_not_root_user

toolchain_setup_env
set -- $newargs
cd $FLAGS_CHROMEOS_DIR/src/scripts
$FLAGS_CHROMEOS_DIR/src/scripts/enter_chroot.sh $newargs
