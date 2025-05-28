import geni.portal as portal
from geni.rspec import RSpec

RSPEC_FILE = "rspecs/profile.xml"

# Create a Request object to start building the RSpec
request = portal.context.makeRequestRSpec()

# Create two raw "PC" nodes
node1 = request.RawPC("node1")
node2 = request.RawPC("node2")

# Set hardware type
node1.hardware_type = "m510"
node2.hardware_type = "m510"

# Set the disk image
node1.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
node2.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"

node1.routable_control_ip = True
node2.routable_control_ip = True

# Create a link between the two nodes
link1 = request.Link(members = [node1, node2])

# Print the RSpec to the console
portal.context.printRequestRSpec()

# Save the RSpec to a file
with open(RSPEC_FILE, "w") as f:
    f.write(request.toXMLString(pretty_print=True, ucode=True))