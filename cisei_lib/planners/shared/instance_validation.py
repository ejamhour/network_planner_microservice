"""Construction-readiness checks independent of link generation and solving."""


def instance_issues(site_nodes):
    """Return (code, field path, message) issues for instantiated equipment.

    Profiles do not count as instances. Disabled interfaces still belong to their
    devices. Compatibility, candidate links and reachability are later stages.
    """
    issues = []
    if not any(site.interfaces for site in site_nodes.values()):
        issues.append(("no_interfaces", "sites",
                       "No interface instances were created. Provide sites/devices with at least one interface."))
    for index, site in enumerate(site_nodes.values()):
        path = f"sites[{index}]"
        devices = {device.device_id for device in site.devices}
        owners = {interface.device_id for interface in site.interfaces}
        if not site.devices:
            issues.append(("no_devices", path + ".devices",
                           f"Site {site.site_id!r} has no device instances."))
        for device_index, device in enumerate(site.devices):
            if device.device_id not in owners:
                issues.append(("device_without_interfaces", f"{path}.devices[{device_index}]",
                               f"Device {device.device_id!r} has no interface instances."))
        for interface_index, interface in enumerate(site.interfaces):
            if interface.device_id not in devices:
                issues.append(("unknown_device", f"{path}.interfaces[{interface_index}].device_id",
                               f"Interface {interface.interface_id!r} references unknown device {interface.device_id!r} at site {site.site_id!r}."))
    return issues
