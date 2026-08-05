# Disposable VM Fixture Policy

Physical VM tests operate only on explicitly disposable, test-owned resources.

## Required identity

Every fixture root must:

- be created beneath a test-owned temporary root;
- contain a machine-readable disposable marker naming the run ID, VM ID,
  creation time, owner, base-image hash, and permitted cleanup root;
- use an immutable verified base image and a per-VM writable overlay;
- allocate distinct state, runtime, socket, port, secret, log, and storage
  paths;
- record every owned path before another phase can observe it.

The normal product policy remains one active AIPC. Multi-VM correctness tests
use an explicit disposable profile and isolated roots; they do not weaken the
normal policy. P1 implements the typed `max_active_vms = 1` setting before any
lifecycle command exists.

## Destruction boundary

Cleanup must resolve the exact marker, VM ID, generation, owned root, and
registry records before deletion. It refuses:

- the operator's production AIPC disk or state root;
- an unmarked or ambiguously marked directory;
- a base image;
- an external imported disk not transferred into managed ownership;
- a symlink, hard-link alias, mount escape, or path outside the fixture root;
- glob-discovered targets.

A partial cleanup produces an ownership report and a failed result. It never
reports success because the intended deletion command was issued.
