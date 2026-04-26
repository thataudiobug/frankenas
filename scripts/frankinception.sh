#!/bin/bash
#
# Ansible Device Management Menu System
# Manages device inventory for DC configurations
#

# Color codes for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if dialog is installed
if ! command -v dialog &> /dev/null; then
    echo -e "${RED}Error: 'dialog' is not installed.${NC}"
    echo "Please install it using: sudo yum install dialog"
    exit 1
fi

# Check if yq is installed (for YAML parsing)
if ! command -v yq &> /dev/null; then
    echo -e "${RED}Error: 'yq' is not installed.${NC}"
    echo "yq is required for YAML parsing."
    echo "Install from internal Amazon repositories or contact your team."
    exit 1
fi

# Base directory for Ansible structure
BASE_DIR="$HOME/frankenas"
DIALOG_TEMP=$(mktemp)
trap "rm -f $DIALOG_TEMP" EXIT

# Global variables for device configuration
declare -A DEVICE_CONFIG
CONTEXT=""
INVENTORY_DIR=""

#######################################
# Utility Functions
#######################################

# Function to check if BASE_DIR exists
check_base_dir() {
    if [ ! -d "$BASE_DIR" ]; then
        dialog --title "Error" \
               --msgbox "Base directory not found: $BASE_DIR\n\nPlease ensure your Ansible structure exists." 10 60
        return 1
    fi
    return 0
}

# Function to read YAML catalog and extract keys
get_catalog_keys() {
    local catalog_file=$1
    local catalog_name=$2
    
    if [ ! -f "$catalog_file" ]; then
        echo ""
        return 1
    fi
    
    # Extract top-level keys under the catalog name
    yq eval ".${catalog_name} | keys | .[]" "$catalog_file" 2>/dev/null
}

# Function to get existing inventory groups
get_inventory_groups() {
    local hosts_file="${INVENTORY_DIR}/hosts.yml"
    
    if [ ! -f "$hosts_file" ]; then
        echo ""
        return 1
    fi
    
    # Extract all group names from hosts.yml
    yq eval 'keys | .[]' "$hosts_file" 2>/dev/null | grep -v "^all$"
}

# Function to read docker catalog items
get_docker_catalog_items() {
    local type=$1  # "groups" or "containers"
    local docker_catalog="${INVENTORY_DIR}/group_vars/docker/docker_catalog.yml"
    
    if [ ! -f "$docker_catalog" ]; then
        echo ""
        return 1
    fi
    
    if [ "$type" == "groups" ]; then
        yq eval '.docker_groups | keys | .[]' "$docker_catalog" 2>/dev/null
    else
        yq eval '.docker_containers | keys | .[]' "$docker_catalog" 2>/dev/null
    fi
}

#######################################
# Device Configuration Functions
#######################################

# Step 1: Select Context (Prod or Test)
select_context() {
    dialog --title "Select Context" \
           --menu "Choose the environment context:" 10 50 2 \
           1 "Production (prod)" \
           2 "Test (test)" 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then
        return 1
    fi
    
    choice=$(cat $DIALOG_TEMP)
    case $choice in
        1)
            CONTEXT="prod"
            ;;
        2)
            CONTEXT="test"
            ;;
        *)
            return 1
            ;;
    esac
    
    INVENTORY_DIR="${BASE_DIR}/inventories/${CONTEXT}"
    
    if [ ! -d "$INVENTORY_DIR" ]; then
        dialog --title "Error" \
               --msgbox "Inventory directory not found: $INVENTORY_DIR" 8 60
        return 1
    fi
    
    DEVICE_CONFIG[context]=$CONTEXT
    return 0
}

# Step 2: Collect Basic Information
collect_basic_info() {
    # 1. Hostname
    dialog --title "Basic Info - Hostname" \
           --inputbox "Enter the hostname:" 10 60 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    DEVICE_CONFIG[host_name]=$(cat $DIALOG_TEMP)
    
    if [ -z "${DEVICE_CONFIG[host_name]}" ]; then
        dialog --msgbox "Hostname cannot be empty!" 6 40
        return 1
    fi
    
    # 2. IP Address
    dialog --title "Basic Info - IP Address" \
           --inputbox "Enter the IP address (ansible_host):" 10 60 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    DEVICE_CONFIG[ansible_host]=$(cat $DIALOG_TEMP)
    
    # 3. Platform
    dialog --title "Basic Info - Platform" \
           --menu "Select the platform type:" 12 60 4 \
           1 "pct" \
           2 "qemu" \
           3 "baremetal" \
           4 "switch" 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    
    case $(cat $DIALOG_TEMP) in
        1) DEVICE_CONFIG[platform]="pct" ;;
        2) DEVICE_CONFIG[platform]="qemu" ;;
        3) DEVICE_CONFIG[platform]="baremetal" ;;
        4) DEVICE_CONFIG[platform]="switch" ;;
    esac
    
    # 4. Root FS Location
    local storage_catalog="${INVENTORY_DIR}/all/storage_catalog.yml"
    local storage_options=$(get_catalog_keys "$storage_catalog" "storage_catalog")
    
    if [ -z "$storage_options" ]; then
        dialog --msgbox "Warning: No storage options found in catalog.\nUsing manual entry." 8 50
        dialog --inputbox "Enter root_fs location:" 10 60 2> $DIALOG_TEMP
        if [ $? -ne 0 ]; then return 1; fi
        DEVICE_CONFIG[root_fs]=$(cat $DIALOG_TEMP)
    else
        # Build menu from storage options
        local menu_items=()
        local counter=1
        while IFS= read -r storage; do
            menu_items+=("$counter" "$storage")
            ((counter++))
        done <<< "$storage_options"
        
        dialog --title "Basic Info - Root FS" \
               --menu "Select root filesystem location:" 15 60 8 \
               "${menu_items[@]}" 2> $DIALOG_TEMP
        
        if [ $? -ne 0 ]; then return 1; fi
        
        local selection=$(cat $DIALOG_TEMP)
        DEVICE_CONFIG[root_fs]=$(echo "$storage_options" | sed -n "${selection}p")
    fi
    
    # 5. Setup Docker?
    dialog --title "Basic Info - Docker" \
           --yesno "Enable Docker setup?" 7 40
    
    if [ $? -eq 0 ]; then
        DEVICE_CONFIG[is_docker]="true"
    else
        DEVICE_CONFIG[is_docker]="false"
    fi
    
    # 6. Advanced Setup?
    dialog --title "Basic Info - Advanced Setup" \
           --yesno "Enable advanced setup?" 7 40
    
    if [ $? -eq 0 ]; then
        DEVICE_CONFIG[is_setup_advanced]="true"
    else
        DEVICE_CONFIG[is_setup_advanced]="false"
    fi
    
    return 0
}

# Step 3: Docker Configuration
collect_docker_config() {
    if [ "${DEVICE_CONFIG[is_docker]}" != "true" ]; then
        return 0
    fi
    
    # Ask management style
    dialog --title "Docker Config - Management Style" \
           --menu "Select Docker management style:" 10 60 2 \
           1 "by_group" \
           2 "by_container" 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    
    case $(cat $DIALOG_TEMP) in
        1) 
            DEVICE_CONFIG[docker_management_style]="by_group"
            local items=$(get_docker_catalog_items "groups")
            local array_name="docker_enabled_groups"
            ;;
        2) 
            DEVICE_CONFIG[docker_management_style]="by_container"
            local items=$(get_docker_catalog_items "containers")
            local array_name="docker_enabled_containers"
            ;;
    esac
    
    if [ -z "$items" ]; then
        dialog --msgbox "Warning: No Docker ${DEVICE_CONFIG[docker_management_style]} found in catalog." 8 60
        return 0
    fi
    
    # Build checklist from items
    local checklist_items=()
    local counter=1
    while IFS= read -r item; do
        checklist_items+=("$counter" "$item" "off")
        ((counter++))
    done <<< "$items"
    
    dialog --title "Docker Config - Select Items" \
           --checklist "Select Docker ${DEVICE_CONFIG[docker_management_style]}:" 20 60 12 \
           "${checklist_items[@]}" 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    
    # Parse selected items
    local selections=$(cat $DIALOG_TEMP)
    local selected_items=()
    
    for sel in $selections; do
        sel=$(echo $sel | tr -d '"')
        selected_items+=($(echo "$items" | sed -n "${sel}p"))
    done
    
    # Store as comma-separated string (will convert to YAML array later)
    DEVICE_CONFIG[$array_name]=$(IFS=,; echo "${selected_items[*]}")
    
    return 0
}

# Step 4: Advanced Configuration
collect_advanced_config() {
    if [ "${DEVICE_CONFIG[is_setup_advanced]}" != "true" ]; then
        return 0
    fi
    
    # 1. Additional Storage
    local storage_catalog="${INVENTORY_DIR}/all/storage_catalog.yml"
    local storage_options=$(get_catalog_keys "$storage_catalog" "storage_catalog")
    
    if [ -n "$storage_options" ]; then
        local checklist_items=()
        local counter=1
        while IFS= read -r storage; do
            checklist_items+=("$counter" "$storage" "off")
            ((counter++))
        done <<< "$storage_options"
        
        dialog --title "Advanced Config - Additional Storage" \
               --checklist "Select additional storage:" 20 60 12 \
               "${checklist_items[@]}" 2> $DIALOG_TEMP
        
        if [ $? -eq 0 ]; then
            local selections=$(cat $DIALOG_TEMP)
            local selected_storage=()
            
            for sel in $selections; do
                sel=$(echo $sel | tr -d '"')
                selected_storage+=($(echo "$storage_options" | sed -n "${sel}p"))
            done
            
            DEVICE_CONFIG[storage_enabled]=$(IFS=,; echo "${selected_storage[*]}")
        fi
    fi
    
    # 2. Additional Networks
    local networks_catalog="${INVENTORY_DIR}/all/networks_catalog.yml"
    local network_options=$(get_catalog_keys "$networks_catalog" "network_catalog")
    
    if [ -n "$network_options" ]; then
        local checklist_items=()
        local counter=1
        while IFS= read -r network; do
            checklist_items+=("$counter" "$network" "off")
            ((counter++))
        done <<< "$network_options"
        
        dialog --title "Advanced Config - Additional Networks" \
               --checklist "Select additional networks:" 20 60 12 \
               "${checklist_items[@]}" 2> $DIALOG_TEMP
        
        if [ $? -eq 0 ]; then
            local selections=$(cat $DIALOG_TEMP)
            local selected_networks=()
            
            for sel in $selections; do
                sel=$(echo $sel | tr -d '"')
                selected_networks+=($(echo "$network_options" | sed -n "${sel}p"))
            done
            
            DEVICE_CONFIG[networks_enabled]=$(IFS=,; echo "${selected_networks[*]}")
        fi
    fi
    
    # 3. Mount GPU?
    dialog --title "Advanced Config - GPU" \
           --yesno "Enable GPU mounting?" 7 40
    
    if [ $? -eq 0 ]; then
        DEVICE_CONFIG[gpu_enabled]="true"
    else
        DEVICE_CONFIG[gpu_enabled]="false"
    fi
    
    return 0
}

# Step 5: Select Inventory Group
select_inventory_group() {
    local groups=$(get_inventory_groups)
    
    if [ -z "$groups" ]; then
        dialog --title "No Groups Found" \
               --inputbox "No existing groups found. Enter a new group name:" 10 60 2> $DIALOG_TEMP
        
        if [ $? -ne 0 ]; then return 1; fi
        DEVICE_CONFIG[inventory_group]=$(cat $DIALOG_TEMP)
        return 0
    fi
    
    # Build menu with existing groups + option to create new
    local menu_items=()
    local counter=1
    while IFS= read -r group; do
        menu_items+=("$counter" "$group")
        ((counter++))
    done <<< "$groups"
    
    menu_items+=("$counter" "** Create New Group **")
    
    dialog --title "Select Inventory Group" \
           --menu "Choose an inventory group for this device:" 20 60 12 \
           "${menu_items[@]}" 2> $DIALOG_TEMP
    
    if [ $? -ne 0 ]; then return 1; fi
    
    local selection=$(cat $DIALOG_TEMP)
    
    if [ "$selection" == "$counter" ]; then
        # Create new group
        dialog --inputbox "Enter new group name:" 10 60 2> $DIALOG_TEMP
        if [ $? -ne 0 ]; then return 1; fi
        DEVICE_CONFIG[inventory_group]=$(cat $DIALOG_TEMP)
    else
        DEVICE_CONFIG[inventory_group]=$(echo "$groups" | sed -n "${selection}p")
    fi
    
    return 0
}

# Step 6: Review Configuration
review_configuration() {
    local review_text="Configuration Review\n\n"
    review_text+="Context: ${DEVICE_CONFIG[context]}\n"
    review_text+="Hostname: ${DEVICE_CONFIG[host_name]}\n"
    review_text+="IP Address: ${DEVICE_CONFIG[ansible_host]}\n"
    review_text+="Platform: ${DEVICE_CONFIG[platform]}\n"
    review_text+="Root FS: ${DEVICE_CONFIG[root_fs]}\n"
    review_text+="Docker Enabled: ${DEVICE_CONFIG[is_docker]}\n"
    
    if [ "${DEVICE_CONFIG[is_docker]}" == "true" ]; then
        review_text+="Docker Style: ${DEVICE_CONFIG[docker_management_style]}\n"
        if [ -n "${DEVICE_CONFIG[docker_enabled_groups]}" ]; then
            review_text+="Docker Groups: ${DEVICE_CONFIG[docker_enabled_groups]}\n"
        fi
        if [ -n "${DEVICE_CONFIG[docker_enabled_containers]}" ]; then
            review_text+="Docker Containers: ${DEVICE_CONFIG[docker_enabled_containers]}\n"
        fi
    fi
    
    review_text+="Advanced Setup: ${DEVICE_CONFIG[is_setup_advanced]}\n"
    
    if [ "${DEVICE_CONFIG[is_setup_advanced]}" == "true" ]; then
        [ -n "${DEVICE_CONFIG[storage_enabled]}" ] && review_text+="Storage: ${DEVICE_CONFIG[storage_enabled]}\n"
        [ -n "${DEVICE_CONFIG[networks_enabled]}" ] && review_text+="Networks: ${DEVICE_CONFIG[networks_enabled]}\n"
        review_text+="GPU Enabled: ${DEVICE_CONFIG[gpu_enabled]}\n"
    fi
    
    review_text+="Inventory Group: ${DEVICE_CONFIG[inventory_group]}\n"
    
    dialog --title "Review Configuration" \
           --yesno "$review_text\n\nDoes this look correct?" 25 70
    
    return $?
}

# Step 7: Write Configuration to Inventory
write_to_inventory() {
    local hosts_file="${INVENTORY_DIR}/hosts.yml"
    local group="${DEVICE_CONFIG[inventory_group]}"
    local hostname="${DEVICE_CONFIG[host_name]}"
    
    # Create backup
    cp "$hosts_file" "${hosts_file}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Build YAML entry
    local yaml_entry="      ${hostname}:\n"
    yaml_entry+="        ansible_host: ${DEVICE_CONFIG[ansible_host]}\n"
    yaml_entry+="        platform: ${DEVICE_CONFIG[platform]}\n"
    yaml_entry+="        root_fs: ${DEVICE_CONFIG[root_fs]}\n"
    yaml_entry+="        is_docker: ${DEVICE_CONFIG[is_docker]}\n"
    yaml_entry+="        is_setup_advanced: ${DEVICE_CONFIG[is_setup_advanced]}\n"
    
    # Add docker config if enabled
    if [ "${DEVICE_CONFIG[is_docker]}" == "true" ]; then
        yaml_entry+="        docker_management_style: ${DEVICE_CONFIG[docker_management_style]}\n"
        
        if [ -n "${DEVICE_CONFIG[docker_enabled_groups]}" ]; then
            yaml_entry+="        docker_enabled_groups:\n"
            IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_groups]}"
            for item in "${ITEMS[@]}"; do
                yaml_entry+="          - $item\n"
            done
        fi
        
        if [ -n "${DEVICE_CONFIG[docker_enabled_containers]}" ]; then
            yaml_entry+="        docker_enabled_containers:\n"
            IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_containers]}"
            for item in "${ITEMS[@]}"; do
                yaml_entry+="          - $item\n"
            done
        fi
    fi
    
    # Add advanced config if enabled
    if [ "${DEVICE_CONFIG[is_setup_advanced]}" == "true" ]; then
        if [ -n "${DEVICE_CONFIG[storage_enabled]}" ]; then
            yaml_entry+="        storage_enabled:\n"
            IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[storage_enabled]}"
            for item in "${ITEMS[@]}"; do
                yaml_entry+="          - $item\n"
            done
        fi
        
        if [ -n "${DEVICE_CONFIG[networks_enabled]}" ]; then
            yaml_entry+="        networks_enabled:\n"
            IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[networks_enabled]}"
            for item in "${ITEMS[@]}"; do
                yaml_entry+="          - $item\n"
            done
        fi
        
        yaml_entry+="        gpu_enabled: ${DEVICE_CONFIG[gpu_enabled]}\n"
    fi
    
    # Check if group exists in hosts.yml
    if yq eval "has(\"$group\")" "$hosts_file" | grep -q "true"; then
        # Group exists, add host to it
        # Use yq to add the host (more reliable than manual editing)
        local temp_file=$(mktemp)
        
        # Create a temporary YAML file with just the host config
        echo "$hostname:" > "$temp_file"
        echo "  ansible_host: ${DEVICE_CONFIG[ansible_host]}" >> "$temp_file"
        echo "  platform: ${DEVICE_CONFIG[platform]}" >> "$temp_file"
        echo "  root_fs: ${DEVICE_CONFIG[root_fs]}" >> "$temp_file"
        echo "  is_docker: ${DEVICE_CONFIG[is_docker]}" >> "$temp_file"
        echo "  is_setup_advanced: ${DEVICE_CONFIG[is_setup_advanced]}" >> "$temp_file"
        
        # Add docker config
        if [ "${DEVICE_CONFIG[is_docker]}" == "true" ]; then
            echo "  docker_management_style: ${DEVICE_CONFIG[docker_management_style]}" >> "$temp_file"
            
            if [ -n "${DEVICE_CONFIG[docker_enabled_groups]}" ]; then
                echo "  docker_enabled_groups:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_groups]}"
                for item in "${ITEMS[@]}"; do
                    echo "    - $item" >> "$temp_file"
                done
            fi
            
            if [ -n "${DEVICE_CONFIG[docker_enabled_containers]}" ]; then
                echo "  docker_enabled_containers:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_containers]}"
                for item in "${ITEMS[@]}"; do
                    echo "    - $item" >> "$temp_file"
                done
            fi
        fi
        
        # Add advanced config
        if [ "${DEVICE_CONFIG[is_setup_advanced]}" == "true" ]; then
            if [ -n "${DEVICE_CONFIG[storage_enabled]}" ]; then
                echo "  storage_enabled:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[storage_enabled]}"
                for item in "${ITEMS[@]}"; do
                    echo "    - $item" >> "$temp_file"
                done
            fi
            
            if [ -n "${DEVICE_CONFIG[networks_enabled]}" ]; then
                echo "  networks_enabled:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[networks_enabled]}"
                for item in "${ITEMS[@]}"; do
                    echo "    - $item" >> "$temp_file"
                done
            fi
            
            echo "  gpu_enabled: ${DEVICE_CONFIG[gpu_enabled]}" >> "$temp_file"
        fi
        
        # Merge into hosts.yml using yq
        yq eval-all "select(fileIndex == 0) * {\"$group\": {\"hosts\": select(fileIndex == 1)}}" \
            "$hosts_file" "$temp_file" > "${hosts_file}.tmp"
        mv "${hosts_file}.tmp" "$hosts_file"
        
        rm "$temp_file"
    else
        # Group doesn't exist, create it
        local temp_file=$(mktemp)
        
        echo "$group:" > "$temp_file"
        echo "  hosts:" >> "$temp_file"
        echo "    $hostname:" >> "$temp_file"
        echo "      ansible_host: ${DEVICE_CONFIG[ansible_host]}" >> "$temp_file"
        echo "      platform: ${DEVICE_CONFIG[platform]}" >> "$temp_file"
        echo "      root_fs: ${DEVICE_CONFIG[root_fs]}" >> "$temp_file"
        echo "      is_docker: ${DEVICE_CONFIG[is_docker]}" >> "$temp_file"
        echo "      is_setup_advanced: ${DEVICE_CONFIG[is_setup_advanced]}" >> "$temp_file"
        
        # Add docker config
        if [ "${DEVICE_CONFIG[is_docker]}" == "true" ]; then
            echo "      docker_management_style: ${DEVICE_CONFIG[docker_management_style]}" >> "$temp_file"
            
            if [ -n "${DEVICE_CONFIG[docker_enabled_groups]}" ]; then
                echo "      docker_enabled_groups:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_groups]}"
                for item in "${ITEMS[@]}"; do
                    echo "        - $item" >> "$temp_file"
                done
            fi
            
            if [ -n "${DEVICE_CONFIG[docker_enabled_containers]}" ]; then
                echo "      docker_enabled_containers:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[docker_enabled_containers]}"
                for item in "${ITEMS[@]}"; do
                    echo "        - $item" >> "$temp_file"
                done
            fi
        fi
        
        # Add advanced config
        if [ "${DEVICE_CONFIG[is_setup_advanced]}" == "true" ]; then
            if [ -n "${DEVICE_CONFIG[storage_enabled]}" ]; then
                echo "      storage_enabled:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[storage_enabled]}"
                for item in "${ITEMS[@]}"; do
                    echo "        - $item" >> "$temp_file"
                done
            fi
            
            if [ -n "${DEVICE_CONFIG[networks_enabled]}" ]; then
                echo "      networks_enabled:" >> "$temp_file"
                IFS=',' read -ra ITEMS <<< "${DEVICE_CONFIG[networks_enabled]}"
                for item in "${ITEMS[@]}"; do
                    echo "        - $item" >> "$temp_file"
                done
            fi
            
            echo "      gpu_enabled: ${DEVICE_CONFIG[gpu_enabled]}" >> "$temp_file"
        fi
        
        # Merge into hosts.yml
        yq eval-all 'select(fileIndex == 0) * select(fileIndex == 1)' \
            "$hosts_file" "$temp_file" > "${hosts_file}.tmp"
        mv "${hosts_file}.tmp" "$hosts_file"
        
        rm "$temp_file"
    fi
    
    dialog --title "Success" \
           --msgbox "Device ${hostname} has been added to inventory!\n\nGroup: ${group}\nContext: ${CONTEXT}\n\nBackup created at:\n${hosts_file}.backup.$(date +%Y%m%d_%H%M%S)" 12 70
    
    return 0
}

#######################################
# Main Function: Build a New Device
#######################################
build_device() {
    # Clear previous config
    unset DEVICE_CONFIG
    declare -gA DEVICE_CONFIG
    
    # Step 1: Select context
    if ! select_context; then
        dialog --msgbox "Device creation cancelled." 6 40
        return
    fi
    
    # Step 2: Collect basic info
    if ! collect_basic_info; then
        dialog --msgbox "Device creation cancelled." 6 40
        return
    fi
    
    # Step 3: Docker config (if enabled)
    if ! collect_docker_config; then
        dialog --msgbox "Device creation cancelled." 6 40
        return
    fi
    
    # Step 4: Advanced config (if enabled)
    if ! collect_advanced_config; then
        dialog --msgbox "Device creation cancelled." 6 40
        return
    fi
    
    # Step 5: Select inventory group
    if ! select_inventory_group; then
        dialog --msgbox "Device creation cancelled." 6 40
        return
    fi
    
    # Step 6: Review configuration
    if ! review_configuration; then
        dialog --yesno "Discard this configuration?" 7 40
        if [ $? -eq 0 ]; then
            dialog --msgbox "Configuration discarded." 6 40
            return
        else
            # Go back to review
            review_configuration || return
        fi
    fi
    
    # Step 7: Write to inventory
    if write_to_inventory; then
        dialog --msgbox "Device successfully added to inventory!" 7 50
    else
        dialog --msgbox "Error: Failed to write to inventory." 7 50
    fi
}

#######################################
# Function: Manage a Device
#######################################
manage_device() {
    dialog --title "Manage a Device" \
           --msgbox "Device management functionality\n\nComing soon..." 10 50
}

#######################################
# Function: Remove a Device
#######################################
remove_device() {
    dialog --title "Remove a Device" \
           --msgbox "Device removal functionality\n\nComing soon..." 10 50
}

#######################################
# Function: Run a Play
#######################################
run_play() {
    dialog --title "
}

#######################################
# Main Menu Loop
#######################################
main_menu() {
    while true; do
        dialog --clear --title "Ansible Device Management System" \
               --menu "Select an option:" 15 60 5 \
               1 "Build a new Device" \
               2 "Manage a Device" \
               3 "Remove a Device" \
               4 "Run a Play" \
               5 "Exit" 2> $DIALOG_TEMP
        
        choice=$?
        if [ $choice -ne 0 ]; then
            clear
            exit 0
        fi
        
        selection=$(cat $DIALOG_TEMP)
        
        case $selection in
            1)
                if check_base_dir; then
                    build_device
                fi
                ;;
            2)
                manage_device
                ;;
            3)
                remove_device
                ;;
            4)
                run_play
                ;;
            5)
                clear
                echo -e "${GREEN}Thank you for using Ansible Device Management System!${NC}"
                exit 0
                ;;
        esac
    done
}

#######################################
# Script Entry Point
#######################################

# Display welcome message
dialog --title "Welcome" \
       --msgbox "Ansible Device Management System\n\nManage your DC configurations with ease.\n\nPress OK to continue..." 10 50

# Start the main menu
main_menu
