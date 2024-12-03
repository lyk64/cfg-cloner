import os
import sys
import time
import codecs
import subprocess
from arbor.modules.ArborDriver import driver
from arbor.domain.ArborDevices import BDF


def initialize_driver():
    return driver()


def get_device_bdf():
    while True:
        bdf_input = raw_input("Enter the device BDF (e.g., 6:0:0): ")
        try:
            bdf_parts = [int(x, 16) for x in bdf_input.split(":")]
            return BDF(*bdf_parts)
        except (ValueError, IndexError):
            print "Invalid BDF format. Please try again."


def create_file_paths(base_dir, prefix):
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return {
        "config": os.path.join(base_dir, "{}_cfgspace_{}.coe".format(prefix, timestamp)),
        "mask": os.path.join(base_dir, "{}_writemask_{}.coe".format(prefix, timestamp)),
    }


def write_headers_to_files(filenames):
    for file_path in filenames.values():
        with codecs.open(file_path, 'w', 'utf-8') as file:
            file.write("memory_initialization_radix=16;\nmemory_initialization_vector=\n\n")


def to_little_endian(hex_value):
    hex_str = hex_value.zfill(8).lower()
    return ''.join(reversed([hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]))


def process_pci_config(drv, bdf, config_space, start, stop, block_size, spacing_pattern):
    """Process PCI configuration space and test writability."""
    lines_config = []
    lines_writemask = []
    current_offset = start
    block_count = 0

    print "Processing PCI configuration space..."
    
    while current_offset <= stop:
        for _ in range(block_size):
            config_block = []
            writemask_block = []

            for _ in range(4):  # Process 4 bytes per line
                config_hex = ""
                writemask_bin = ""

                for _ in range(4):  # Process each byte in the 4-byte block
                    try:
                        byte_addr = current_offset
                        current_value = config_space[byte_addr]

                        byte_mask = ""

                        for bit_pos in range(8):  # Process each bit in the byte
                            mask = 1 << bit_pos
                            try:
                                # Flip the bit
                                test_value = current_value ^ mask
                                drv.writePciConfig(bdf, byte_addr, 1, test_value)
                                updated_value = drv.readPciConfig(bdf, byte_addr, 1)

                                if updated_value == test_value:  # Verify the bit was successfully flipped
                                    # Restore the original value
                                    drv.writePciConfig(bdf, byte_addr, 1, current_value)
                                    restored_value = drv.readPciConfig(bdf, byte_addr, 1)

                                    byte_mask += '1' if restored_value == current_value else '0'

                                else:
                                    byte_mask += '0'  # Flip failed, treat as read-only
                            except Exception as e:
                                print "Error processing byte 0x{:03X}, bit {}: {}".format(byte_addr, bit_pos, e)
                                byte_mask += '0'  # Treat as read-only on error

                        writemask_bin = byte_mask + writemask_bin
                        config_hex = "{:02x}".format(current_value) + config_hex
                        current_offset += 1

                    except IndexError as e:
                        print "Error: Invalid offset or config space out of range: {}".format(e)
                        break

                config_block.append(to_little_endian(config_hex))
                writemask_block.append("{:08x}".format(int(writemask_bin, 2)))

            lines_config.append(",".join(config_block) + ",\n")
            lines_writemask.append(",".join(writemask_block) + ",\n")

        # Add blank lines based on spacing pattern
        blank_lines = "\n" * spacing_pattern[block_count % len(spacing_pattern)]
        lines_config.append(blank_lines)
        lines_writemask.append(blank_lines)
        block_count += 1

    print "PCI Configuration processed successfully."
    return lines_config, lines_writemask


def save_processed_data(filenames, config_lines, mask_lines):
    """Write processed data to output files."""
    if not config_lines or not mask_lines:
        print "Error: No data to write to files. Check the processing logic."
        return

    with codecs.open(filenames["config"], 'a', 'utf-8') as config_file:
        config_file.writelines(config_lines)
        config_file.write(";")

    with codecs.open(filenames["mask"], 'a', 'utf-8') as mask_file:
        mask_file.writelines(mask_lines)
        mask_file.write(";")


def main():

    drv = initialize_driver()
    bdf = get_device_bdf()
    base_dir = "C:\\MindShare\\Arbor\\python"
    filenames = create_file_paths(base_dir, "pcileech")

    write_headers_to_files(filenames)

    config_space = drv.readConfigSpace(bdf)
    config_space_length = len(config_space) if config_space else 256

    start, stop = 0x000, config_space_length - 16
    block_size = 16
    spacing_pattern = [1, 1, 1, 3, 1, 1, 1, 2, 1, 1, 1, 3, 1, 1, 1]

    config_lines, mask_lines = process_pci_config(
        drv, bdf, config_space, start, stop, block_size, spacing_pattern
    )

    save_processed_data(filenames, config_lines, mask_lines)

    subprocess.Popen(r'explorer /select,"{}"'.format(filenames["config"]))
    sys.exit()


if __name__ == "__main__":
    main()
