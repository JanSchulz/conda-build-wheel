'''
Module that does most of the heavy lifting for the ``conda build-wheel`` command.
'''
from __future__ import absolute_import, division, print_function

import os
import sys
import fnmatch
from glob import glob
import shutil
from os.path import exists, isdir, isfile, join, basename

import conda.plan as plan
from conda.install import  linked, move_to_trash

from conda_build import environ, source
from conda_build.config import config
from conda_build.utils import rm_rf, _check_call

from conda_build.build import create_env, get_build_index, prefix_files

on_win = (sys.platform == 'win32')
if 'bsd' in sys.platform:
    shell_path = '/bin/sh'
else:
    shell_path = '/bin/bash'


def build(m, verbose=True,  channel_urls=(),  override_channels=False, wheel_dir="./build"):
    '''
    Build the package with the specified metadata.
    :param m: Package metadata
    :type m: Metadata
    '''

    if (m.get_value('build/detect_binary_files_with_prefix')
        or m.binary_has_prefix_files()):
        # We must use a long prefix here as the package will only be
        # installable into prefixes shorter than this one.
        config.use_long_build_prefix = True
    else:
        # In case there are multiple builds in the same process
        config.use_long_build_prefix = False

    if m.skip():
        print("Skipped: The %s recipe defines build/skip for this "
              "configuration." % m.dist())
        return

    print("Removing old build environment")
    if on_win:
        if isdir(config.short_build_prefix):
            move_to_trash(config.short_build_prefix, '')
        if isdir(config.long_build_prefix):
            move_to_trash(config.long_build_prefix, '')
    else:
        rm_rf(config.short_build_prefix)
        rm_rf(config.long_build_prefix)
    print("Removing old work directory")
    if on_win:
        if isdir(source.WORK_DIR):
            move_to_trash(source.WORK_DIR, '')
    else:
        rm_rf(source.WORK_DIR)

    # Display the name only
    # Version number could be missing due to dependency on source info.
    print("BUILD START:", m.dist())
    create_env(config.build_prefix,
        [ms.spec for ms in m.ms_depends('build')],
        verbose=verbose, channel_urls=channel_urls,
        override_channels=override_channels)

    if m.name() in [i.rsplit('-', 2)[0] for i in linked(config.build_prefix)]:
        print("%s is installed as a build dependency. Removing." %
            m.name())
        index = get_build_index(clear_cache=False, channel_urls=channel_urls, override_channels=override_channels)
        actions = plan.remove_actions(config.build_prefix, [m.name()], index=index)
        assert not plan.nothing_to_do(actions), actions
        plan.display_actions(actions, index)
        plan.execute_actions(actions, index)

    # downlaod source code...
    source.provide(m.path, m.get_section('source'))

    # Parse our metadata again because we did not initialize the source
    # information before.
    m.parse_again()

    print("Package:", m.dist())

    assert isdir(source.WORK_DIR)
    src_dir = source.get_dir()
    contents = os.listdir(src_dir)
    if contents:
        print("source tree in:", src_dir)
    else:
        print("no source")

    rm_rf(config.info_dir)
    files1 = prefix_files()
    for pat in m.always_include_files():
        has_matches = False
        for f in set(files1):
            if fnmatch.fnmatch(f, pat):
                print("Including in package existing file", f)
                files1.discard(f)
                has_matches = True
        if not has_matches:
            sys.exit("Error: Glob %s from always_include_files does not match any files" % pat)
    # Save this for later
    with open(join(config.croot, 'prefix_files.txt'), 'w') as f:
        f.write(u'\n'.join(sorted(list(files1))))
        f.write(u'\n')
    print("Source dir: %s" % src_dir)
    if sys.platform == 'win32':
        windows_build(m)
    else:
        env = environ.get_dict(m)
        build_file = join(m.path, 'build_wheel.sh')

        if not isfile(build_file):
            print("Using plain 'python setup.py bdist_wheel'  as build script")
            build_file = join(src_dir, 'build_wheel.sh')
            with open(build_file, 'w') as fo:
                fo.write('\n')
                fo.write('# Autogenerated build command:\n')
                fo.write('python setup.py bdist_wheel\n')
                fo.write('\n')

        cmd = [shell_path, '-x', '-e', build_file]
        _check_call(cmd, env=env, cwd=src_dir)

    all_wheels = glob(join(src_dir, "dist", '*.whl'))
    if len(all_wheels) == 0:
        print("No wheels produced!")
    else:
        if len(all_wheels) == 1:
            print("More than one wheel produced!")
        try:
            os.makedirs(wheel_dir)
            print("Created wheel dir: %s:" % wheel_dir)
        except OSError:
            if not isdir(wheel_dir):
                raise
        print("Copying to %s:" % wheel_dir)
        for wheel in all_wheels:
            shutil.copy(wheel, wheel_dir)
            print(" %s" % basename(wheel))


def windows_build(m):
    from conda_build.windows import msvc_env_cmd, kill_processes

    env = dict(os.environ)
    env.update(environ.get_dict(m))
    env = environ.prepend_bin_path(env, config.build_prefix, True)

    for name in 'BIN', 'INC', 'LIB':
        path = env['LIBRARY_' + name]
        if not isdir(path):
            os.makedirs(path)

    src_dir = source.get_dir()
    bld_bat = join(m.path, 'bld_wheel.bat')
    if exists(bld_bat):
        with open(bld_bat) as fi:
            data = fi.read()
    else:
        print("Using plain 'python setup.py bdist_wheel'  as build script")
        data = "\n:: Autogenerated build command:\npython setup.py bdist_wheel\n"

    with open(join(src_dir, 'bld.bat'), 'w') as fo:
        fo.write('@echo on\n')
        fo.write(msvc_env_cmd(override=m.get_value('build/msvc_compiler', None)))
        fo.write('\n')
        # more debuggable with echo on
        fo.write('set\n')
        fo.write('where python\n')
        fo.write('@echo on\n')
        fo.write("set INCLUDE={};%INCLUDE%\n".format(env["LIBRARY_INC"]))
        fo.write("set LIB={};%LIB%\n".format(env["LIBRARY_LIB"]))
        fo.write("REM ===== end generated header =====\n")
        fo.write(data)

    cmd = [os.environ['COMSPEC'], '/c', 'call', 'bld.bat']
    print("build cmd: %s" % cmd)
    _check_call(cmd, cwd=src_dir, env={str(k): str(v) for k, v in env.items()})
    kill_processes()
