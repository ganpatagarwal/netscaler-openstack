#!/usr/bin/env bash
#the script should be inside the bundle directory
#run the script as bash <scriptname> --ip=<controlcenter_ip> --password=<password> --protocol=<protocol>

undo_driver_changes_v1 () {
cp ./netscaler_driver_before_driver_patch_$curr_time $path_v1/netscaler_driver.py
cp ./ncc_client_before_driver_patch_$curr_time $path_v1/ncc_client.py
}

undo_driver_changes_v2 () {
cp ./netscaler_driver_v2_before_driver_patch_$curr_time $path_v2/netscaler_driver_v2.py
cp ./ncc_client_before_driver_patch_$curr_time $path_v1/ncc_client.py
}

revert_conf_changes () {
cp ./neutron.conf_before_driver_patch_$curr_time /etc/neutron/neutron.conf
cp ./neutron_lbaas.conf_before_driver_patch_$curr_time /etc/neutron/neutron_lbaas.conf
}

is_netscaler_default () {
echo -n "Do you want to make NetScaler default service provider (Y/N)?"
read response
if [ "$response" == "y" ] || [ "$response" == "Y" ]
then
	return 1
else 
	return 0
fi
}


make_netscaler_default() {
serv_prov_line="${serv_prov_line}:default"
}

choose_default()
{
default_providers=`grep "^[[:space:]]*service_provider[[:space:]]*=[[:space:]]*LOADBALANCER.*" /etc/neutron/neutron_lbaas.conf | grep -v "NetScaler" | grep -v "#" | grep ":default"| wc -l`
echo "count of other default service providers is " $default_providers
if [ $default_providers -gt 0 ]
then
	is_netscaler_default
	if [ $? -eq 1 ]
	then 
        sed -i -e '/^[[:space:]]*service_provider[[:space:]]*=[[:space:]]*LOADBALANCER/s/:default//g' /etc/neutron/neutron_lbaas.conf
		make_netscaler_default
	fi
else
	make_netscaler_default	
fi
}

#checking if 3 arguments have been given to the script

usage_message="USAGE: ./install.sh --ip=<controlcenter_ip> --password=<password> --protocol=<protocol> [--neutron-lbaas-path=<neutron-lbaas-path>]\nExample for using option neutron-lbaas-path \n 1) for devstack :  --neutron-lbaas-path=/opt/stack/neutron-lbaas \n 2) for multinode openstack setup : --neutron-lbaas-path=/usr/lib/python2.7/site-packages"
if [ "$#" -lt 3 ]; then
echo -e $usage_message >&2
exit
fi

for i in "$@"
do
case $i in
    --ip=*)
    CCIP="${i#*=}"
    shift # past argument=value
    ;;
    --password=*)
    PASSWORD="${i#*=}"
    shift # past argument=value
    ;;
    --protocol=*)
    PROTOCOL="${i#*=}"
    shift # past argument=value
    ;;
    --neutron-lbaas-path=*)
    NEUTRON_LBAAS_PATH="${i#*=}"
    shift
    ;;
    *)
     echo "unknown argument passed"
     echo -e $usage_message >&2
     exit
    ;;
esac
done

if [ -z $CCIP ] || [ -z $PASSWORD ] || [ -z $PROTOCOL ]
then
echo -e $usage_message >&2
exit
fi

PROTOCOL=`echo $PROTOCOL | tr '[:upper:]' '[:lower:]'`

if [ $PROTOCOL == http ]
then
	port=80
elif [ $PROTOCOL == https ]
then
	port=443
else
	echo "Invalid protocol.
Protocol should be either http or https.
Exiting.." >&2
	exit
fi

#checking if the version is v1 or v2 or if the service_plugins is not present

echo "checking for LBaaS configuration in neutron.conf"
conf_file="/etc/neutron/neutron.conf"

v2_cnt=`grep service_plugins $conf_file | grep 'neutron_lbaas.services.loadbalancer.plugin.LoadBalancerPluginv2\|[=,]\s*lbaasv2\s*\(,\|$\)' | grep -v "#" | wc -l`
echo "count of lbaas v2 plugin entries in $conf_file : $v2_cnt"
v1_cnt=`grep service_plugins $conf_file | grep 'neutron_lbaas.services.loadbalancer.plugin.LoadBalancerPlugin\|[=,]\s*lbaas\s*\(,\|$\)\|neutron.services.loadbalancer.plugin.LoadBalancerPlugin' | grep -v LoadBalancerPluginv2 | grep -v "#" | wc -l`
echo "count of lbaas v1 plugin entries in $conf_file : $v1_cnt"
if [ $v2_cnt == 1 ] && [ $v1_cnt == 0 ]
then
echo "Identified setup is working with LBaaS v2"
openstack_mode=v2
elif [ $v2_cnt == 0 ] && [ $v1_cnt == 1 ]
then
echo "Identified setup is working with LBaaS v1"
openstack_mode=v1
else 
echo "LBaaS is not properly configured in this setup.
Insert the required configuration line in neutron.conf
exiting.." >&2
exit
fi

fixed_path_v1="neutron_lbaas/services/loadbalancer/drivers/netscaler"
fixed_path_v2="neutron_lbaas/drivers/netscaler"

if [ -z "$NEUTRON_LBAAS_PATH" ]
then
IFS_store=${IFS}
    IFS="
"
echo "Searching for Netscaler Driver"
#storing location of netscaler_driver for v1
target_path=($(find / -path "*/$fixed_path_v1" 2> /dev/null))

#storing location of netscaler_driver for v2
target_path_v2=($(find / -path "*/$fixed_path_v2" 2> /dev/null))

IFS=${IFS_store}
if [[ ( "$openstack_mode" == v2 &&  -z "$target_path_v2") || ( -z  "$target_path" ) ]]
then
echo " Could not locate netscaler driver files, please provide the path of neutron as a
parameter using  --neutron-lbaas-path" >&2
exit
elif [[ ( "$openstack_mode" == v2 && "${#target_path_v2[@]}" -gt 1 ) || ( ${#target_path[@]} -gt 1 ) ]] 
then
echo "Multiple netscaler driver directories found, please provide the path of neutron as a parameter using  --neutron-lbaas-path" >&2
exit
else
echo "Found NetScaler driver files at location"
path_v1=${target_path[0]}
if [ "$openstack_mode" == v1 ]
then  echo "$path_v1"
else 
path_v2=${target_path_v2[0]} && echo "$path_v2"
fi
fi
else
if [ -d "$NEUTRON_LBAAS_PATH/$fixed_path_v1" ]
then
path_v1="$NEUTRON_LBAAS_PATH/$fixed_path_v1"
else
echo "The directory in the neutron-lbaas-path entered does not exist" >&2
exit
fi
if [ "$openstack_mode" == v2 ] 
then 
if [ -d "$NEUTRON_LBAAS_PATH/$fixed_path_v2" ]
then
path_v2="$NEUTRON_LBAAS_PATH/$fixed_path_v2"
else
echo "The directory in the neutron-lbaas-path entered does not exist." >&2
exit
fi
fi
fi

echo "creating backup of neutron.conf and neutron_lbaas.conf files"
curr_time=`date '+%Y_%m_%d_%H_%M_%S'`
cp /etc/neutron/neutron.conf ./neutron.conf_before_driver_patch_$curr_time
cp /etc/neutron/neutron_lbaas.conf ./neutron_lbaas.conf_before_driver_patch_$curr_time

echo "creating backup of netscaler_driver files in the current directory"

if [ $openstack_mode == v1 ];then
cp $path_v1/netscaler_driver.py ./netscaler_driver_before_driver_patch_$curr_time
cp $path_v1/ncc_client.py ./ncc_client_before_driver_patch_$curr_time
else
cp $path_v2/netscaler_driver_v2.py ./netscaler_driver_v2_before_driver_patch_$curr_time
cp $path_v1/ncc_client.py ./ncc_client_before_driver_patch_$curr_time
fi

checkprovider=`grep "\[service_providers\]" /etc/neutron/neutron_lbaas.conf | grep -v "#" | wc -l`
if [ $checkprovider != 1 ]
then
echo "no section for service providers exists in neutron_lbaas.conf
exiting.." >&2
exit
fi
echo "deleting previous Netscaler service provider entry"
sed -i /service_provider[[:space:]]*=[[:space:]]*LOADBALANCER:NetScaler:neutron_lbaas.services.loadbalancer.drivers.netscaler.netscaler_driver.NetScalerPluginDriver/d /etc/neutron/neutron_lbaas.conf
sed -i /service_provider[[:space:]]*=[[:space:]]*LOADBALANCERV2:NetScaler:neutron_lbaas.drivers.netscaler.netscaler_driver_v2.NetScalerLoadBalancerDriverV2/d /etc/neutron/neutron_lbaas.conf


if [ $openstack_mode == v1 ];then
echo "deleting previous driver files"

rm -rf $path_v1/netscaler_driver.py $path_v1/netscaler_driver.pyc 2> /dev/null
rm -rf $path_v1/ncc_client.py $path_v1/ncc_client.pyc 2> /dev/null

echo "Replacing netscaler driver files at location $path_v1 with driver files from bundle"
cp ./ncc_client.py $path_v1/
return_status_for_ncc_client=$?
cp ./v1/netscaler_driver.py $path_v1/
return_status_for_driver_v1=$?

if [ $return_status_for_ncc_client != 0 ] || [ $return_status_for_driver_v1 != 0 ]
then
echo "Error:: Unable to patch netscaler driver files please re-run the script again with user that has credentials to patch files."
echo "reverting driver changes" >&2
undo_driver_changes_v1
revert_conf_changes
exit
fi

echo "Configuring netscaler driver configuration in neutron_lbaas.conf"
#echo "adding NetscalerPluginDriver entry to the list of service providers in neutron_lbaas.conf"
serv_prov_line="service_provider=LOADBALANCER:NetScaler:neutron_lbaas.services.loadbalancer.drivers.netscaler.netscaler_driver.NetScalerPluginDriver"
choose_default
awk '/^\s*\[service_providers\]/ { print; print "'"$serv_prov_line"'"; next }1' /etc/neutron/neutron_lbaas.conf > /etc/neutron/neutron_lbaas_temp.conf
if [ $? != 0 ]
then
echo "Error in adding service provider.Reverting changes in neutron_lbaas.conf" >&2
undo_driver_changes_v1
revert_conf_changes
rm /etc/neutron/neutron_lbaas_temp.conf
fi

mv /etc/neutron/neutron_lbaas_temp.conf /etc/neutron/neutron_lbaas.conf


else

echo "deleting previous driver files"

rm -rf $path_v2/netscaler_driver_v2.py $path_v1/netscaler_driver_v2.pyc 2> /dev/null
rm -rf $path_v1/ncc_client.py $path_v1/ncc_client.pyc 2> /dev/null


echo "Replacing netscaler driver files at location $path_v2 and $path_v1  with driver files from bundle"

cp ./ncc_client.py $path_v1/
return_status_for_ncc_client=$?
cp ./v2/netscaler_driver_v2.py $path_v2/
return_status_for_driver_v2=$?
if [ $return_status_for_ncc_client != 0 ] || [ $return_status_for_driver_v2 != 0 ]
then
echo "Error:: Unable to patch netscaler driver files please re-run the script again with user that has credentials to patch files."
echo "reverting driver changes" >&2
undo_driver_changes_v2
revert_conf_changes
exit
fi

echo "Configuring netscaler driver configuration in neutron_lbaas.conf"
#echo "adding  NetscalerLoadBalancerDriverV2 entry to the list of service providers in neutron_lbaas.conf"
serv_prov_line="service_provider=LOADBALANCERV2:NetScaler:neutron_lbaas.drivers.netscaler.netscaler_driver_v2.NetScalerLoadBalancerDriverV2"
choose_default
awk '/^\s*\[service_providers\]/ { print; print "'"$serv_prov_line"'"; next }1' /etc/neutron/neutron_lbaas.conf > /etc/neutron/neutron_lbaas_temp.conf
if [ $? != 0 ]
then
echo "Error in adding service provider.Reverting changes in neutron_lbaas.conf" >&2
undo_driver_changes_v2
revert_conf_changes
rm /etc/neutron/neutron_lbaas_temp.conf
fi
mv /etc/neutron/neutron_lbaas_temp.conf /etc/neutron/neutron_lbaas.conf
fi

#echo "adding the [netscaler_driver] section in neutron.conf"
echo "Configuring controlcenter access details in neutron.conf"
str="[netscaler_driver]\nnetscaler_ncc_uri = $PROTOCOL://$CCIP:$port \n#NetScaler driver user credentials. You must specify the same login credentials in NetScaler Control Center. \n# The NetScaler Control Center authenticates calls from the OpenStack NetScaler driver by using these login credentials. \n#netscaler_ncc_username must be set to openstack_driver. For netscaler_ncc_password, provide a password of your choice. \nnetscaler_ncc_username = openstack_driver \nnetscaler_ncc_password = $PASSWORD \nnetscaler_ncc_cleanup_mode = False \n#<setting> = <time interval for periodic tasks (in seconds)> \n# Interval in which status of LB entities are refreshed.\nperiodic_task_interval = 20 \n#<setting>=<True/False(For enabling or disabling status collection), default page_size> \nnetscaler_status_collection=True,300 \nis_synchronous = False"

sed -n '/\[netscaler_driver\]/,$ p' $conf_file | sed -n '2,$ p'| sed -n '/\[/, $ p' > append.txt
a=$?
sed -e '/\[netscaler_driver\]/,$ d' $conf_file > /etc/neutron/neutrontmp.conf
b=$?
mv /etc/neutron/neutrontmp.conf $conf_file
echo -e $str >> $conf_file
cat append.txt >> $conf_file 
rm -f append.txt

echo "checking neutron service"
service neutron-server status 2> /dev/null
ret=$?
if [ $ret != 0 ] 
then
#echo "neutron-server is unrecognized, please restart neutron manually"
echo ":NOTE: neutron-server service is not found. Please restart neutron server. If openstack setup is based on devstack, you can login to neutron’s screen and restart neutron server" >&2
else
echo "Found neutron-server service"
echo -n "Neutron server has to be restarted to make netscaler driver files effective. Restart neutron-server (Y/N)?"
read response

if [ "$response" == "y" ] || [ "$response" == "Y" ]
then
service neutron-server stop

service neutron-server start
return_status=$?
if [ $return_status != 0 ] || [ $a != 0 ] || [ $b != 0 ]
then
echo "Failed to restart neutron server
reverting changes" >&2
revert_conf_changes
if [ "$openstack_mode" == v1 ]
then
undo_driver_changes_v1
else
undo_driver_changes_v2
fi
fi

fi

fi

