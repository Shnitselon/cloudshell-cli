from cloudshell.cli.session_handler import SessionHandler
from cloudshell.cli.session_state_wrapper import SessionModeWrapper
from logging import Logger
class Cli(object):
    SSH = 'ssh'
    TELNET = 'telnet'
    TCP = 'tcp'

    def __init__(self):


        self.logger = Logger('logger')

    def new_session(self,session_type,ip,default_mode,port='',user='',password=''):
        session_handler = SessionHandler()
        session,connection_manager = session_handler.initiate_connection_manager(self.logger,session_type,ip,port,user,password,default_mode)
        return SessionModeWrapper(session, connection_manager,default_mode)









if __name__ == '__main__':
    #default_mode = Command_mode('[>$#]/s*$', 'enter', 'exit')
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('192.168.28.150', 22, 'root', 'Juniper')
    cli=Cli()
    with cli.new_session(session_type=Cli.SSH,ip='192.168.28.150',user='root',password='Juniper', default_mode = 'root@%') as default_session:
        print default_session
        default_session.send_command('show version',state.default)
