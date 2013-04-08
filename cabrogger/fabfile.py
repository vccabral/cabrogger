"""Deployment management for KnightLab web application projects.

Add the pem file to your ssh agent:

    ssh-add <pemfile>

Set your AWS credentials in environment variables:
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY

or in config files:
    /etc/boto.cfg, or
    ~/.boto

Note: Do not quote key strings in the config files.

For AWS (boto) config details, see:
    http://boto.readthedocs.org/en/latest/boto_config_tut.html#credentials

Set a WORKON_HOME environment variable.  This is the root directory for all
of your virtual environments.  If you use virtualenvwrapper, this is already
set for you. If not, then set it manually.

USAGE:

fab <env> <operation>

i.e.: fab [stg|prd] [setup|hosts]

This will execute the operation for all web servers for the given environment
(stg, or prd).

To execute commands for a specific host, specify apps user @ remote host:

    fab -H apps@<public-dns> <env> <operation>
"""
import fnmatch
import os
import re
import sys
import tempfile
import boto
from random import choice
from boto import ec2
from fabric.api import env, put, require, run, sudo
from fabric.context_managers import cd, prefix
from fabric.contrib.files import exists
from fabric.colors import red
from fabric.utils import warn
from fabric.tasks import execute
from fabric.decorators import roles, runs_once

PROJECT_NAME = 'cabrogger'

# EXISTING SYSTEM AND REPO RESOURCES
APP_USER = 'apps'
PYTHON = 'python2.7'
REPO_URL = 'git@github.com:NUKnightLab/%s.git' % PROJECT_NAME
CONF_DIRNAME = 'conf' # should contain stg & prd directories
APACHE_CONF_NAME = 'apache' # inside conf/stg, conf/prd
APACHE_MAINTENANCE_CONF_NAME = 'apache.maintenance'
SSH_DIRNAME = '.ssh'
USERS_HOME = '/home'
STATIC_DIRNAME = 'static'
VIRTUALENV_SYSTEM_SITE_PACKAGES = False

# DEPLOYMENT SETTINGS
SITES_DIRNAME = 'sites'
LOG_DIRNAME = 'log'
ENV_DIRNAME = 'env' # virtualenvs go here
AWS_CREDENTIALS_ERR_MSG = """
    Unable to connect to AWS. Check your credentials. boto attempts to
    find AWS credentials in environment variables AWS_ACCESS_KEY_ID
    and AWS_SECRET_ACCESS_KEY, or in config files: /etc/boto.cfg, or
    ~/.boto. Do not quote key strings in config files. For details, see:
    http://boto.readthedocs.org/en/latest/boto_config_tut.html#credentials
"""

_ec2_con = None
_s3_con = None


def _do(yes_no):
    """Boolean for yes/no values."""
    return yes_no.lower().startswith('y')


def _confirm(msg):
    """Get confirmation from the user."""
    return _do(raw_input(msg))


def _get_ec2_con():
    global _ec2_con
    if _ec2_con is None:
        try:
            _ec2_con = boto.connect_ec2()
        except boto.exception.NoAuthHandlerFound:
            print AWS_CREDENTIALS_ERR_MSG
            sys.exit(0)
    return _ec2_con


def _get_s3_con():
    global _s3_con
    if _s3_con is None:
        try:
            _s3_con = boto.connect_s3()
        except boto.exception.NoAuthHandlerFound:
            print AWS_CREDENTIALS_ERR_MSG
            sys.exit(0)
    return _s3_con
        

def _path(*args):
    return os.path.join(*args)


env.project_name = PROJECT_NAME
env.repo_url = REPO_URL
env.home_path = _path(USERS_HOME, APP_USER)
env.ssh_path = _path(env.home_path, SSH_DIRNAME)
env.sites_path = _path(env.home_path, SITES_DIRNAME)
env.log_path = _path(env.home_path, LOG_DIRNAME, PROJECT_NAME)
env.project_path = _path(env.sites_path, env.project_name)
env.conf_path = _path(env.project_path, CONF_DIRNAME)
env.env_path = _path(env.home_path, ENV_DIRNAME)
env.ve_path = _path(env.env_path, env.project_name)
env.activate_path = _path(env.ve_path, 'bin', 'activate')
env.apache_path = _path(env.home_path, 'apache')
env.python = PYTHON
env.app_user = APP_USER
env.roledefs = { 'app':[], 'work':[], 'pgis':[], 'mongo':[] }


def _copy_from_s3(bucket_name, resource, dest_path):
    """Copy a resource from S3 to a remote file."""
    bucket = _get_s3_con().get_bucket(bucket_name)
    key = bucket.lookup(resource)
    f = tempfile.NamedTemporaryFile(delete=False)
    key.get_file(f)
    f.close()
    put(f.name, dest_path)
    os.unlink(f.name)


def _setup_ssh():
    # TODO: should this be part of provisioning?
    with cd(env.ssh_path):
        if not exists('known_hosts'):
            _copy_from_s3('knightlab.ops', 'deploy/ssh/known_hosts',
                os.path.join(env.ssh_path, 'known_hosts'))
        if not exists('config'):
            _copy_from_s3('knightlab.ops', 'deploy/ssh/config',
                os.path.join(env.ssh_path, 'config'))
        if not exists('github.key'):
            # TODO: make github.key easily replaceable
            _copy_from_s3('knightlab.ops', 'deploy/ssh/github.key',
                os.path.join(env.ssh_path, 'github.key'))
            with cd(env.ssh_path):
                run('chmod 0600 github.key')
     

def _setup_directories():
    run('mkdir -p %(sites_path)s' % env)
    run('mkdir -p %(log_path)s' %env)
    run('mkdir -p %(ve_path)s' % env)


def _setup_virtualenv():
    """Create a virtualenvironment."""
    if VIRTUALENV_SYSTEM_SITE_PACKAGES:
        run('virtualenv -p %(python)s --system-site-packages %(ve_path)s' % env)
    else:
        run('virtualenv -p %(python)s %(ve_path)s' % env)


def _clone_repo():
    """Clone the git repository."""
    run('git clone %(repo_url)s %(project_path)s' % env)


def _run_in_ve(command):
    """Execute the command inside the virtualenv."""
    with prefix('. %s' % env.activate_path):
        run(command)


def _run_in_ve_local(command):
    """Execute the command inside the local virtualenv."""
    workon_home = os.getenv('WORKON_HOME')
    if not workon_home:
        abort('WORKON_HOME environment variable is not set!')
    activate_path = _path(workon_home, env.project_name, 'bin/activate')
    local('source %s && ' % activate_path + command )

    
def _install_requirements():
    with cd(env.project_path):
        if exists('requirements.txt'):
            _run_in_ve('pip install -r requirements.txt')


def _symlink(existing, link):
    """Removes link if it exists and creates the specified link."""
    if exists(link):
        run('rm %s' % link)
    run('ln -s %s %s' % (existing, link))


@roles('app')
def _link_apache_conf(maint=False):
    if maint:
        link_file = APACHE_MAINTENANCE_CONF_NAME
    else:
        link_file = APACHE_CONF_NAME
    apache_conf = _path(env.conf_path, env.settings, link_file)
    if exists(apache_conf):
        run('mkdir -p %(apache_path)s' % env)
        link_path = _path(env.apache_path, env.project_name)
        _symlink(apache_conf, link_path)


def _get_ec2_reservations():
    try:
        return _get_ec2_con().get_all_instances()
    except boto.exception.EC2ResponseError, e:
        print "\nReceived error from AWS. Are your credentials correct?"
        print "Note: do not quote keys in boto config files."
        print "\nError from Amazon was:\n"
        print e
        sys.exit(0)


def _lookup_ec2_instances():
    """Get the EC2 instances by role definition for this working environment 
    and load them into env.roledefs"""
    regex = re.compile(r'%s-(?P<role>[a-zA-Z]+)[0-9]+' % env.settings)
    for r in _get_ec2_reservations():
        for i in r.instances:
            m = regex.match(i.tags.get('Name', ''))
            if m:
                env.roledefs[m.group('role')].append(
                    '%s@%s' % (env.app_user, i.public_dns_name) 
                )


def _setup_env(env_type):
    """Setup the working environment as appropriate for stg, prd."""
    env.settings = env_type
    if not env.hosts: # allow user to specify hosts
        _lookup_ec2_instances()
    

def prd():
    """Work on production environment."""
    _setup_env('prd')


def stg():
    """Work on staging environment."""
    _setup_env('stg')


def _random_key(length=50,
        chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'):
    return ''.join([choice(chars) for i in range(length)])


def genkey():
    """Generate a random key."""
    print
    print _random_key()


def _build_django_siteconf():
    require('settings', provided_by=[prd, stg])
    secret_key = _random_key()
    run("""echo "SECRET_KEY='%s'" >> %s""" % (secret_key,
        os.path.join(env.project_path, 'core', 'settings', 'site.py')))


def _setup_project(django='y'):
    """Setup new application deployment.  Run only once per project."""
    require('settings', provided_by=[prd, stg])
    _setup_ssh()
    _setup_directories()
    _clone_repo()
    _setup_virtualenv()
    _build_django_siteconf()
    _install_requirements()

       
@roles('app','work')
def setup(django='y'):
    """Setup new application deployment. Run only once per project."""
    require('settings', provided_by=[prd, stg])
    execute(_setup_project, django=django)
    execute(_link_apache_conf)


def hosts():
    """List applicable hosts for the specified environment.
    Use this to determine which hosts will be affected by an
    environment-specific operation."""
    require('settings', provided_by=[prd, stg])
    for host in env.instances:
        print env.hosts


@roles('app', 'work')    
def checkout():
    """Pull the latest code on remote servers."""
    # TODO: Support branching? e.g.:
    #run('cd %(repo_path)s; git checkout %(branch)s; git pull origin %(branch)s' % env)
    run('cd %(project_path)s; git pull' % env)


@roles('app')    
def a2start():
    """Start apache.
    apache2ctl start does not seem to work through fabric, thus uses init.d
    """
    require('settings', provided_by=[prd, stg])
    run('sudo /etc/init.d/apache2 start')


@roles('app')    
def a2stop(graceful='y'):
    """Stop apache. Defaults to graceful stop. Specify graceful=n for
    immediate stop."""
    require('settings', provided_by=[prd, stg])
    if _do(graceful):
        run('sudo /usr/sbin/apache2ctl graceful-stop')
    else:
        run('sudo /usr/sbin/apache2ctl stop')

    
@roles('app')    
def a2restart(graceful='y'):
    """Restart apache. Defaults to graceful restart. Specify graceful=n for
    immediate restart."""
    require('settings', provided_by=[prd, stg])
    if _do(graceful):
        run('sudo /usr/sbin/apache2ctl graceful')
    else:
        run('sudo /usr/sbin/apache2ctl restart')


@roles('app')    
def mrostart():
    """Start maintenance mode (maintenance/repair/operations)."""
    require('settings', provided_by=[prd, stg])
    _link_apache_conf(maint=True)
    a2restart()


@roles('app')    
def mrostop():
    """End maintenance mode."""
    require('settings', provided_by=[prd, stg])
    _link_apache_conf()
    a2restart()


@runs_once
def deploystatic(fnpattern='*'):
    """Copy local static files to S3. Does not perform remote server operations.
    Takes an optional filename matching pattern fnpattern. Requires that the
    local git repository has no uncommitted changes."""
    git_status = os.popen('git status').read()
    ready_status = '# On branch master\nnothing to commit'
    if True or git_status.startswith(ready_status):
        print 'deploying to S3 ...'
        if env.settings == 'stg':
            bucket_name = 'media.knilab.com'
        elif env.settings == 'prd':
            bucket_name = 'media.knightlab.us'
        else:
            assert False, "Unhandled bucket_name condition"
        bucket = _get_s3_con().get_bucket(bucket_name)
        matched_file = False
        for path, dirs, files in os.walk(STATIC_DIRNAME):
            for f in fnmatch.filter(files, fnpattern):
                matched_file = True
                dest = os.path.join(env.project_name,
                    path[len(STATIC_DIRNAME)+1:], f)
                print 'Copying file to %s:%s' % (bucket_name, dest)
                key = boto.s3.key.Key(bucket)
                key.key = dest
                fn = os.path.join(path, f)
                key.set_contents_from_filename(fn)
                key.set_acl('public-read')
        if not matched_file:
            print '\nNothing to deploy'
    else:
        print """
        You have uncommitted local code changes. Please commit and push
        changes before deploying to S3."""

def deploy(mro='y', restart='y', static='y', requirements='n'):
    """Deploy the latest version of the site to the server. Defaults to
    setting maintenance mode during the deployment and restarting apache."""
    require('settings', provided_by=[prd, stg])
    if _do(mro):
        mrostart()
    #require('branch', provided_by=[stable, master, branch])
    checkout()
    if _do(requirements):
        _install_requirements()
    if _do(static):
        deploystatic()
    if _do(restart):
        if _do(mro):
            mrostop()
        else:
            a2restart()
    # TODO: south_migrations()


@roles('app', 'work')
def destroy():
    """Remove the project directory and config files."""
    require('settings', provided_by=[prd, stg])
    msg = """
        This will remove all %(project_name)s project files for %(settings)s."""
    warn(red(msg % env))
    msg = """
        Destroy %(project_name)s project %(settings)s deployment? (y/n) """
    if not _confirm(msg % env):
        print "aborting ..."
        return
    apache_link = _path(env.apache_path, env.project_name)
    if exists(apache_link):
        run('rm %s' % apache_link)
    run('rm -rf %(project_path)s' % env) 
    run('rm -rf %(log_path)s' % env) 
    run('rm -rf %(ve_path)s' % env)
