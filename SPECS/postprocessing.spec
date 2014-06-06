Summary: postprocessing
Name: postprocessing
Version: 2.0
Release: 0
Group: Applications/Engineering
prefix: /opt/postprocessing
BuildRoot: %{_tmppath}/%{name}
License: Open
Source: postprocessing.tgz
Requires: libNeXus.so.0()(64bit) libc.so.6()(64bit) libc.so.6(GLIBC_2.2.5)(64bit)
#Requires: mantid 
#Requires: mantidunstable 
#Requires: mantidnightly
#Requires: python-suds 
#Requires: python-stompest 
#Requires: python-stompest.async
#Requires: python-requests
%define debug_package %{nil}


%description
Post-processing agent to automatically catalog and reduce neutron data

%prep
%setup -q -n %{name}

%build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}%{_sysconfdir}
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{prefix}
make prefix="%{buildroot}%{prefix}" sysconfig="%{buildroot}%{_sysconfdir}/autoreduce" bindir="%{buildroot}/%{_bindir}" install

%post
chgrp snswheel %{_sysconfdir}/autoreduce/icat4.cfg
chgrp snswheel %{_sysconfdir}/autoreduce/icatclient.properties
chgrp snswheel %{_sysconfdir}/autoreduce/post_process_consumer.conf
chown snsdata %{_sysconfdir}/autoreduce
chown snsdata %{prefix}

%files
%config %{_sysconfdir}/autoreduce/icat4.cfg
%config %{_sysconfdir}/autoreduce/icatclient.properties
%config %{_sysconfdir}/autoreduce/post_process_consumer.conf
%attr(755, -, -) %{prefix}/postprocessing/
%attr(755, -, -) %{prefix}/scripts/remoteJob.sh
%attr(755, -, -) %{prefix}/scripts/startJob.sh
%attr(755, -, -) %{_bindir}/queueProcessor.py