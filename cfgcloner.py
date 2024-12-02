import psutil  # For monitoring system resources
import os
import sys
import time
import codecs
import subprocess
import logging
import gc  # For garbage collection
from arbor.modules.ArborDriver import driver
from arbor.domain.ArborDevices import BDF

# Setup primary logging for debugging
logging.basicConfig(
    filename="debugging.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Setup a separate logger for system resources
resource_logger = logging.getLogger("resource_logger")
resource_handler = logging.FileHandler("resource_monitoring.log")
resource_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
resource_logger.addHandler(resource_handler)
resource_logger.setLevel(logging.INFO)


def log_memory_usage():
    """Log the current memory and system resource usage."""
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / (1024 ** 2)  # Convert to MB
    cpu_usage = process.cpu_percent(interval=0.1)
    resource_logger.info("Memory Usage: %.2f MB | CPU Usage: %.2f%%" % (memory_usage, cpu_usage))


def initialize_driver():
    """Initialize the PCI driver."""
    logging.debug("Initializing PCI driver.")
    log_memory_usage()
    return driver()


def confirm_proceed():
    """Display a warning about disrupting critical hardware and ask for confirmation."""
    warning_message = (
        "\nWARNING: This script can disrupt critical hardware on your system.\n"
        "Please ensure you have correctly identified the BDF of a non-critical PCI or PCIe device.\n"
        "Proceeding with an incorrect BDF can lead to system instability or data loss.\n"
        "Do you want to continue? Type 'YES' to proceed, or anything else to exit: "
    )
    user_input = raw_input(warning_message)  # Use raw_input for Python 2.7
    logging.debug("User input for confirmation: {}".format(user_input))
    if user_input.strip().upper() != "YES":
        logging.warning("Operation canceled by user.")
        print "Operation canceled."
        sys.exit(0)


def get_device_bdf():
    """Prompt the user for a BDF string and return a BDF object."""
    while True:
        bdf_input = raw_input("Enter the device BDF (e.g., 4:0:0): ")  # Use raw_input for Python 2.7
        logging.debug("User entered BDF: {}".format(bdf_input))
        try:
            bdf_parts = [int(x, 16) for x in bdf_input.split(":")]
            bdf = BDF(*bdf_parts)
            logging.debug("Parsed BDF object: {}".format(bdf))
            log_memory_usage()
            return bdf
        except (ValueError, IndexError):
            logging.error("Invalid BDF format entered: {}".format(bdf_input))
            print "Invalid BDF format. Please try again."


def create_file_paths(base_dir, prefix):
    """Create and return file paths with a timestamped prefix."""
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logging.debug("Created base directory: {}".format(base_dir))
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    paths = {
    "config": os.path.join(base_dir, "{}_cfgspace_{}.coe".format(prefix, timestamp)),
    "mask": os.path.join(base_dir, "{}_writemask_{}.coe".format(prefix, timestamp)),
}
    logging.debug("Generated file paths: {}".format(paths))
    return paths


def write_headers_to_files(filenames):
    """Write file headers to specified files."""
    logging.debug("Writing headers to output files.")
    for file_path in filenames.values():
        with codecs.open(file_path, 'w', 'utf-8') as file:
            file.write("memory_initialization_radix=16;\nmemory_initialization_vector=\n\n")
            logging.debug("Written header to file: {}".format(file_path))


def to_little_endian(hex_value):
    """Convert a hex value to little-endian format."""
    hex_str = hex_value.zfill(8).lower()
    result = ''.join(reversed([hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]))
    logging.debug("Converted {} to little-endian: {}".format(hex_value, result))
    return result


def process_pci_config(drv, bdf, config_space, start, stop, block_size, spacing_pattern):
    """Process PCI configuration space and test writability."""
    lines_config = []
    lines_writemask = []
    current_offset = start
    block_count = 0

    logging.info("Starting PCI configuration space processing.")
    
    while current_offset <= stop:
        logging.debug("Processing offset: {}".format(current_offset))
        try:
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

                                    if updated_value == test_value:  # Verify the bit was flipped successfully
                                        # Restore the original value
                                        drv.writePciConfig(bdf, byte_addr, 1, current_value)
                                        restored_value = drv.readPciConfig(bdf, byte_addr, 1)

                                        if restored_value == current_value:  # Verify restoration succeeded
                                            byte_mask += '1'  # Writable
                                        else:
                                            byte_mask += '0'  # Restoration failed
                                    else:
                                        byte_mask += '0'  # Flip failed
                                except Exception as e:
                                    logging.error("Error processing byte 0x{:03X}, bit {}: {}".format(byte_addr, bit_pos, e))
                                    byte_mask += '0'  # Treat as read-only on error

                            writemask_bin = byte_mask + writemask_bin
                            config_hex = "{:02x}".format(current_value) + config_hex
                            current_offset += 1

                        except IndexError as e:
                            logging.error("Invalid offset or config space out of range: {}".format(e))
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

        except Exception as e:
            logging.error("Error during processing at offset {}: {}".format(current_offset, e))

        # Perform garbage collection and log system resources
        gc.collect()
        logging.debug("Garbage collection completed.")
        log_memory_usage()

    logging.info("Finished PCI Configuration processing.")
    return lines_config, lines_writemask


def save_processed_data(filenames, config_lines, mask_lines):
    """Write processed data to output files."""
    logging.info("Saving processed data to files.")
    if not config_lines or not mask_lines:
        logging.error("No data to write to files. Check the processing logic.")
        return

    with codecs.open(filenames["config"], 'a', 'utf-8') as config_file:
        config_file.writelines(config_lines)
        config_file.write(";")
        logging.debug("Config data saved to {}".format(filenames['config']))

    with codecs.open(filenames["mask"], 'a', 'utf-8') as mask_file:
        mask_file.writelines(mask_lines)
        mask_file.write(";")
        logging.debug("Mask data saved to {}".format(filenames['mask']))


def main():
    """Main function to execute the script."""
    logging.info("Starting main script.")
    confirm_proceed()

    drv = initialize_driver()
    bdf = get_device_bdf()
    base_dir = "C:\\MindShare\\Arbor\\python"
    filenames = create_file_paths(base_dir, "pcileech")

    write_headers_to_files(filenames)

    config_space = drv.readConfigSpace(bdf)
    config_space_length = len(config_space) if config_space else 256

    start, stop = 0x000, config_space_length - 16
    block_size = 16  # Keep the original processing logic intact
    spacing_pattern = [1, 1, 1, 3, 1, 1, 1, 2, 1, 1, 1, 3, 1, 1, 1]

    config_lines, mask_lines = process_pci_config(
        drv, bdf, config_space, start, stop, block_size, spacing_pattern
    )

    save_processed_data(filenames, config_lines, mask_lines)

    subprocess.Popen(r'explorer /select,"{}"'.format(filenames["config"]))
    logging.info("Script completed successfully.")
    sys.exit()


if __name__ == "__main__":
    main()
