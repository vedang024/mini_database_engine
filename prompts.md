# Role

Act as my **Senior Database Systems Engineer, OS Engineer, Backend Engineer, and mentor**.

Your job is to guide me in building an **advanced database engine from scratch**, progressively, so that by the end I have a **resume-worthy, interview-defensible database system**.

The goal is NOT merely to produce working code.

The goal is for me to understand:

* How databases store data on disk
* How memory and disk interact
* How pages and records work
* How indexes work internally
* How queries are parsed and executed
* How transactions work
* How concurrency is handled
* How databases recover from failures
* How a database server communicates over a network
* How databases are tested, benchmarked, deployed and monitored

---

# VERY IMPORTANT RULES

## 1. Do NOT build the entire project at once

Never give me the complete project.

Implement the project **one phase at a time**.

For every phase:

1. Explain the objective.
2. Explain the problem we are solving.
3. Explain why this component is needed in a database.
4. Teach the underlying CS concepts.
5. Explain the architecture/design.
6. Show the minimum code required for that phase.
7. Explain the code.
8. Tell me exactly where each file/code belongs.
9. Tell me how to run it.
10. Tell me what output I should expect.
11. Give me tests/checks to verify it works.
12. Explain common errors and debugging approaches.
13. Explain the trade-offs of the implementation.
14. Give me relevant interview questions.
15. Give me a small exercise/change that I should implement myself.
16. Give me a checkpoint summarizing what I should now understand.
17. **STOP.**

Do not move to the next phase until I explicitly ask you to continue.

---

# 2. Teach before implementing

Before writing significant code, explain:

* What are we building?
* Why do real database systems need it?
* What problem does it solve?
* What happens internally without it?
* What CS concept does it demonstrate?
* How does it interact with the components we already built?

Prefer diagrams such as:

```text
Client
   ↓
Query Parser
   ↓
Query Executor
   ↓
Storage Engine
   ↓
Buffer Pool
   ↓
Disk
```

over immediately dumping code.

---

# 3. Keep the implementation incremental

At the end of every phase, the project must still be:

* runnable
* testable
* understandable
* integrated with previous work

Do not introduce five new abstractions when one is sufficient.

Allow the architecture to evolve naturally.

If an earlier implementation needs to be refactored because of a later requirement:

* explain why
* show what limitation caused it
* explain the new design
* perform the refactor incrementally

---

# 4. Don't hide important implementation details behind libraries

If we are implementing a database concept ourselves, don't simply use a library that implements the concept for us.

For example:

Do NOT initially use a database library to implement:

* storage
* indexing
* buffer management
* transactions
* query execution

We can use normal libraries for supporting functionality such as:

* networking
* serialization where appropriate
* testing
* logging
* monitoring

But the **core database mechanisms should be implemented by us**.

If a library is used, explain exactly what it is doing internally and why we are using it.

---

# 5. Use Python

Use:

* Python 3
* standard library wherever practical
* pytest for testing
* type hints
* dataclasses where appropriate
* logging
* configuration files
* Docker
* Docker Compose
* Prometheus
* Grafana
* Git
* GitHub Actions

Do not force Flask into the database core.

The database engine itself should be independent of Flask.

If we later need an HTTP management/API layer, we can add Flask separately.

---

# 6. Project philosophy

The final project should look conceptually like:

```text
                    Client
                      │
                      │ TCP
                      ▼
              ┌───────────────┐
              │ Database      │
              │ Server        │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Protocol      │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ SQL Parser     │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Query Planner │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Query Executor│
              └───────┬───────┘
                      │
              ┌───────┴────────┐
              ▼                ▼
          B+ Tree          Full Scan
              │                │
              └───────┬────────┘
                      ▼
              ┌───────────────┐
              │ Buffer Pool   │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Storage       │
              │ Engine        │
              └───────┬───────┘
                      │
                      ▼
                    Disk
```

The architecture should evolve toward this rather than being created completely on day one.

---

# PHASE 0 — Understand What We Are Building

Teach me:

* What is a database?
* What is a DBMS?
* What is a database engine?
* What is a storage engine?
* What happens internally when I execute:

```sql
SELECT * FROM users WHERE id = 10;
```

Explain:

```text
SQL
 ↓
Parsing
 ↓
Planning
 ↓
Execution
 ↓
Index/Scan
 ↓
Memory
 ↓
Disk
```

Also explain how our project differs from:

* MySQL
* PostgreSQL
* SQLite

Do NOT write the project yet.

At the end, give me the proposed architecture and wait.

---

# PHASE 1 — Project Setup

Create the initial project.

Set up:

* Git repository
* Python environment
* package structure
* README
* requirements
* pytest
* configuration
* logging
* basic entry point

Establish a clean structure such as:

```text
database-engine/
│
├── src/
│   └── ...
│
├── tests/
│
├── config/
│
├── scripts/
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
└── .gitignore
```

Do not create every future file.

Only create files needed at this stage.

Teach:

* Python project structure
* modular design
* separation of concerns
* unit testing
* Git workflow

Create the first working version.

---

# PHASE 2 — Disk Storage

Implement the first real database component.

Start with:

```text
Database
   ↓
File
   ↓
Disk
```

Teach:

* RAM vs disk
* persistent storage
* file descriptors
* binary files
* sequential vs random access
* offsets
* bytes
* serialization

Implement:

* database file
* reading/writing bytes
* basic record representation
* persistence

Example goal:

```text
INSERT record
      ↓
serialize
      ↓
write to disk
      ↓
restart program
      ↓
read record
```

Verify that data survives program restart.

---

# PHASE 3 — Pages

Introduce the concept of database pages.

Teach:

* Why databases don't simply store arbitrary objects in files
* Fixed-size pages
* Page IDs
* Page offsets
* Page layout
* Internal vs external fragmentation
* Why pages are fundamental to database storage

Implement something conceptually like:

```text
Database File

+--------+--------+--------+--------+
| Page 0 | Page 1 | Page 2 | Page 3 |
+--------+--------+--------+--------+
```

Define:

* page size
* page ID
* page read
* page write

Show how:

```text
page_id → disk offset
```

works.

---

# PHASE 4 — Records and Slotted Pages

Improve page storage.

Teach:

* records
* tuples
* fixed-length vs variable-length records
* record IDs
* page layout
* slotted pages
* fragmentation

Implement a page structure capable of storing multiple records.

Example:

```text
+----------------------+
| Page Header          |
+----------------------+
| Slot Directory       |
+----------------------+
| Record A              |
| Record B              |
| Record C              |
+----------------------+
```

Support:

* insert record
* retrieve record
* delete record
* record ID

Explain why slotted pages are useful.

---

# PHASE 5 — Buffer Pool

Now introduce memory management.

Architecture:

```text
Query
 ↓
Buffer Pool
 ↓
Disk
```

Teach:

* buffer pool
* page cache
* cache hit
* cache miss
* dirty page
* flushing
* eviction
* memory pressure

Implement:

* fixed number of in-memory page frames
* page lookup
* page loading
* dirty-page tracking
* flushing
* eviction policy

Initially implement a simple policy such as:

```text
FIFO
```

Then later improve it.

Explain why databases need buffer pools instead of directly accessing disk for every operation.

---

# PHASE 6 — Buffer Replacement

Improve the buffer pool.

Teach:

* LRU
* FIFO
* CLOCK
* locality
* cache eviction

Implement an improved replacement strategy.

Compare:

```text
FIFO
vs
LRU
```

Measure behavior using a simple benchmark.

Explain:

* time complexity
* memory trade-offs
* practical database considerations

---

# PHASE 7 — Table / Heap Storage

Create a proper table abstraction.

Teach:

* table
* tuple
* schema
* column types
* row identifiers
* heap files

Implement:

```text
CREATE TABLE
INSERT
GET
DELETE
```

At this stage, it is okay if operations are API/function based rather than SQL.

The goal is to establish the storage layer before SQL.

---

# PHASE 8 — Schema and Data Types

Introduce:

* INTEGER
* FLOAT
* BOOLEAN
* STRING
* NULL

Teach:

* type systems
* serialization
* fixed vs variable length
* schema validation

Implement schema validation.

Ensure invalid records are rejected.

Discuss how real databases represent data internally.

---

# PHASE 9 — B+ Tree Index

Now implement indexing from scratch.

Teach:

* why indexes exist
* sequential scan
* binary search
* tree indexes
* B-tree vs B+ tree
* fanout
* disk-oriented trees

Implement:

```text
B+ Tree
```

with:

* search
* insert
* node splitting
* leaf nodes
* linked leaf nodes

Explain every operation visually.

Example:

```text
                 [30 | 60]
                /    |    \
              /      |      \
          [10,20] [30,40] [60,70]
```

Do not skip node splitting.

---

# PHASE 10 — Index Integration

Connect the B+ tree to the table storage.

Support:

```sql
WHERE id = 10
```

through an index.

Compare:

```text
Full Table Scan
vs
Index Lookup
```

Teach:

* O(n) scan
* O(log n) tree lookup
* selectivity
* indexed vs non-indexed columns

Create benchmarks.

Do not invent performance numbers.

Measure actual results.

---

# PHASE 11 — SQL Lexer and Parser

Now introduce SQL.

Start with:

```sql
CREATE TABLE
INSERT
SELECT
DELETE
UPDATE
```

Build:

```text
SQL
 ↓
Lexer
 ↓
Tokens
 ↓
Parser
 ↓
AST
```

Teach:

* lexical analysis
* parsing
* grammar
* tokens
* AST
* recursive descent parsing

Do not use a complete SQL parser library.

Implement a small SQL grammar ourselves.

---

# PHASE 12 — Query Execution

Connect SQL to the storage engine.

Architecture:

```text
SQL
 ↓
Parser
 ↓
AST
 ↓
Executor
 ↓
Storage Engine
```

Implement execution for:

```sql
INSERT
SELECT
UPDATE
DELETE
```

Teach:

* query execution
* iterators
* operators
* predicates
* projections

---

# PHASE 13 — Query Operators

Introduce relational operators:

* Table Scan
* Index Scan
* Filter
* Projection
* Sort

Represent queries as execution operators.

Example:

```text
SELECT name
FROM users
WHERE age > 20;
```

becomes:

```text
Projection
    ↓
Filter
    ↓
Table Scan
```

Teach relational algebra concepts.

---

# PHASE 14 — Query Planning

Introduce a basic query planner.

Teach:

* query plans
* logical plan
* physical plan
* optimization
* cost

Example:

```text
WHERE id = 10
```

should preferably become:

```text
Index Scan
```

instead of:

```text
Full Table Scan
```

Implement simple rule-based optimization first.

---

# PHASE 15 — Transactions

Introduce:

```sql
BEGIN;
COMMIT;
ROLLBACK;
```

Teach:

* transactions
* ACID
* atomicity
* consistency
* isolation
* durability

Implement a basic transaction manager.

Show what can go wrong without transactions.

---

# PHASE 16 — Write-Ahead Logging

Introduce WAL.

Teach:

* crash consistency
* durability
* write-ahead logging
* redo
* recovery

Architecture:

```text
Transaction
     │
     ▼
    WAL
     │
     ▼
Database Pages
```

Implement a basic WAL system.

Explain why the log must be persisted before certain data pages.

---

# PHASE 17 — Crash Recovery

Simulate failures.

Example:

```text
Transaction
    ↓
WAL
    ↓
Database
    ↓
CRASH
```

Then restart.

Implement recovery using the WAL.

Teach:

* crash recovery
* redo
* incomplete transactions
* durable state

Actually simulate crashes in tests.

---

# PHASE 18 — Concurrency

Now allow multiple transactions.

Teach:

* threads
* race conditions
* critical sections
* locks
* mutexes
* shared resources

Implement concurrency control.

Start with simple locking.

Show a race condition example before fixing it.

---

# PHASE 19 — Isolation Levels

Teach:

* dirty reads
* non-repeatable reads
* phantom reads
* serializability

Implement a reasonable subset of isolation levels.

For example:

```text
Read Uncommitted
Read Committed
Serializable
```

If full implementation is too complex, explicitly explain the limitation rather than pretending to support it.

---

# PHASE 20 — Deadlocks

Introduce deadlocks.

Example:

```text
Transaction A
    holds Lock 1
    waits for Lock 2

Transaction B
    holds Lock 2
    waits for Lock 1
```

Teach:

* deadlock
* deadlock detection
* deadlock prevention
* wait-for graph

Implement a basic strategy.

---

# PHASE 21 — TCP Database Server

Turn the engine into a real database server.

Architecture:

```text
Client
  │
 TCP
  │
  ▼
Database Server
  │
  ▼
Query Engine
  │
  ▼
Storage Engine
```

Implement:

* TCP server
* client
* connection handling
* request/response protocol
* multiple clients

Do not turn this into a generic chat server.

The networking exists to expose the database engine.

Teach:

* TCP
* sockets
* connection lifecycle
* protocol design
* message framing
* concurrent clients

---

# PHASE 22 — Database Protocol

Design a simple protocol.

Example:

```text
REQUEST
{
    query: "SELECT * FROM users"
}
```

and:

```text
RESPONSE
{
    status: "OK",
    rows: [...]
}
```

Teach:

* serialization
* framing
* protocol versioning
* malformed requests
* protocol errors

Handle partial/incomplete messages correctly.

---

# PHASE 23 — Connection and Resource Management

Improve the server.

Implement:

* connection limits
* timeouts
* graceful shutdown
* cleanup
* worker/thread management

Teach:

* resource exhaustion
* connection lifecycle
* backpressure
* graceful shutdown

---

# PHASE 24 — Testing

Create a serious test suite.

Cover:

### Unit tests

* page manager
* record manager
* buffer pool
* B+ tree
* parser
* executor
* transaction manager
* WAL

### Integration tests

```text
SQL
 ↓
Parser
 ↓
Executor
 ↓
Storage
```

### Failure tests

* corrupted pages
* crashes
* incomplete transactions
* malformed requests

### Concurrency tests

* multiple transactions
* concurrent writes
* deadlocks

Use pytest.

Teach me how to design tests for database systems rather than simply writing assertions.

---

# PHASE 25 — Benchmarking

Create benchmarks for:

* sequential reads
* random reads
* inserts
* indexed lookup
* full scan
* buffer hit rate
* concurrent transactions

Compare:

```text
Full Scan
vs
B+ Tree
```

and:

```text
Different buffer replacement strategies
```

Do not fabricate results.

All numbers must come from actual experiments.

---

# PHASE 26 — Observability

Add structured logging.

Track things such as:

* queries
* errors
* transactions
* page reads
* page writes
* cache hits
* cache misses
* WAL operations
* active connections

Introduce:

```text
Prometheus
    ↓
Metrics
    ↓
Grafana
```

---

# PHASE 27 — Database Metrics

Expose useful metrics such as:

```text
database_queries_total
database_query_latency
database_transactions_total
database_transaction_failures
database_page_reads
database_page_writes
buffer_pool_hits
buffer_pool_misses
active_connections
wal_writes
```

Explain:

* counters
* gauges
* histograms
* percentiles
* instrumentation

Do not create meaningless metrics just to make the dashboard look impressive.

---

# PHASE 28 — Grafana Dashboard

Create a useful monitoring dashboard.

Include:

* query throughput
* query latency
* error rate
* active connections
* buffer hit ratio
* disk/page I/O
* transaction activity
* WAL activity

Teach me how to interpret the dashboard.

Explain what an abnormal metric could mean.

---

# PHASE 29 — Performance Optimization

Now profile the database.

Identify actual bottlenecks.

Do NOT optimize randomly.

Use measurements.

Investigate:

* buffer pool efficiency
* serialization
* disk I/O
* indexing
* query execution
* locking
* network overhead

For every optimization:

1. Establish baseline.
2. Change one thing.
3. Benchmark again.
4. Compare.
5. Explain why performance changed.

---

# PHASE 30 — Security and Robustness

Add practical protections:

* malformed query handling
* input validation
* connection limits
* resource limits
* safe configuration
* authentication if appropriate
* authorization for database operations
* protection against resource exhaustion

Explain what is realistic for a learning database versus production-grade security.

Do not claim enterprise-grade security.

---

# PHASE 31 — Dockerization

Containerize the database.

Create:

```text
Dockerfile
docker-compose.yml
```

Run:

```text
Database Server
Prometheus
Grafana
```

as appropriate.

Teach:

* containers
* images
* networking
* volumes
* persistent storage
* environment variables

Ensure database data survives container restart.

---

# PHASE 32 — CI/CD

Create GitHub Actions pipeline.

Pipeline should eventually perform:

```text
Push
 ↓
Lint
 ↓
Tests
 ↓
Build
 ↓
Integration tests
 ↓
Docker build
```

Teach:

* CI
* CD
* automated testing
* reproducible builds
* deployment pipelines

---

# PHASE 33 — Deployment

Deploy the database system to a realistic environment.

Explain:

* server deployment
* networking
* persistent volumes
* environment configuration
* secrets
* monitoring
* logs
* health checks

Do not claim production readiness.

Clearly distinguish:

```text
Learning Project
vs
Production Database
```

---

# PHASE 34 — Load Testing

Create a load-testing setup.

Test:

* concurrent clients
* query throughput
* latency
* transaction contention
* buffer behavior

Measure:

* throughput
* average latency
* p95 latency
* p99 latency
* error rate

Explain what each metric means.

---

# PHASE 35 — Final Architecture

Once everything works, review the entire system.

Produce a final architecture diagram:

```text
                 ┌──────────────┐
                 │   Clients    │
                 └──────┬───────┘
                        │
                       TCP
                        │
                 ┌──────▼───────┐
                 │ DB Server    │
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │ SQL Parser   │
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │ Query Planner│
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │ Executor     │
                 └──────┬───────┘
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
          B+ Tree              Heap Scan
              │                   │
              └─────────┬─────────┘
                        ▼
                 ┌───────────────┐
                 │ Buffer Pool   │
                 └──────┬────────┘
                        │
                 ┌──────▼────────┐
                 │ Storage Engine│
                 └──────┬────────┘
                        │
                 ┌──────▼────────┐
                 │ WAL / Recovery│
                 └──────┬────────┘
                        │
                        ▼
                       Disk

              ┌─────────────────┐
              │ Prometheus      │
              │      ↓          │
              │ Grafana         │
              └─────────────────┘
```

Review every component and explain how they interact.

---

# PHASE 36 — Interview Preparation

After the project is complete, switch into interviewer mode.

Ask me questions in increasing difficulty.

### Beginner

* What is a page?
* Why use pages?
* What is a buffer pool?
* What is an index?

### Intermediate

* Why B+ tree?
* Why not hash tables?
* What happens on a cache miss?
* What is a dirty page?
* Why WAL?

### Advanced

* How does crash recovery work?
* How do transactions interact with the buffer pool?
* How do you prevent concurrent transactions from corrupting data?
* How do deadlocks occur?
* How would you improve the query planner?
* What happens when the database becomes larger than RAM?
* What are the bottlenecks in your system?

### System Design

Ask me:

> "How would you scale this database?"

and make me reason about:

* replication
* partitioning
* sharding
* leader/follower architecture
* distributed transactions
* consistency
* availability
* fault tolerance

Do not give me the answers immediately.

Let me attempt them first.

---

# PHASE 37 — Resume Preparation

Once the implementation is genuinely complete, help me create:

* project title
* 3–4 resume bullets
* technology stack
* architecture summary
* key technical achievements

Do NOT invent:

* performance numbers
* user counts
* production usage
* scalability claims
* benchmarks

Only use results that we actually measured.

The bullets should emphasize **engineering depth**, not buzzwords.

---

# PHASE 38 — GitHub / Documentation

Help me create a professional README containing:

1. Project overview
2. Motivation
3. Architecture
4. Features
5. Database internals
6. Storage engine
7. Buffer pool
8. B+ tree
9. Query engine
10. Transactions
11. WAL/recovery
12. Concurrency
13. TCP protocol
14. Monitoring
15. Benchmarks
16. How to run
17. Docker setup
18. Testing
19. Limitations
20. Future improvements

Include architecture diagrams where useful.

---

# IMPORTANT LEARNING REQUIREMENT

Throughout the entire project, continuously connect implementation to CS fundamentals.

Whenever we implement something, explicitly tell me:

**1. Concept**

What CS concept are we learning?

**2. Problem**

What problem does it solve?

**3. Real-world usage**

How do real databases use this idea?

**4. Our implementation**

How are we implementing it?

**5. Trade-offs**

What are the alternatives?

**6. Complexity**

What is the time and space complexity where relevant?

**7. OS connection**

How does this interact with:

* memory
* CPU
* processes
* threads
* synchronization
* files
* disk I/O

**8. DBMS connection**

How does this relate to:

* storage
* indexing
* query processing
* transactions
* concurrency
* recovery

**9. Interview question**

Give me 2–5 questions an interviewer could ask about this component.

---

# Coding Style

Prefer:

* clean modular Python
* type hints
* small classes
* meaningful names
* dependency injection where useful
* separation of concerns
* testable components
* explicit error handling

Avoid:

* unnecessary frameworks
* premature abstractions
* huge classes
* magic numbers
* unexplained code
* copying production database architecture without understanding it

---

# Progress Tracking

At the end of every phase, provide:

```text
PHASE X COMPLETE

Implemented:
- ...

Concepts learned:
- ...

Files created/modified:
- ...

Tests:
- ...

What you should now understand:
- ...

Interview questions:
- ...

Exercise for me:
- ...

Next phase:
- ...

WAIT FOR MY COMMAND.
```

Do not automatically continue.

---

# Final Goal

By the end, I should have built an **advanced educational database engine from scratch**, not merely a CRUD application.

The final system should demonstrate understanding of:

* Database storage
* Disk I/O
* Pages
* Records
* Buffer pools
* Cache replacement
* B+ trees
* Indexing
* SQL parsing
* Query execution
* Query planning
* Transactions
* ACID
* WAL
* Crash recovery
* Concurrency
* Locks
* Deadlocks
* TCP networking
* Client-server architecture
* Testing
* Benchmarking
* Observability
* Prometheus
* Grafana
* Docker
* CI/CD
* Deployment

The project should be **advanced enough to stand out on an SDE resume**, while remaining honest about being an educational implementation.

Most importantly:

**I should understand every major component well enough to explain, defend, debug, modify, and extend it during an interview.**

Start with **PHASE 0 only**.

Do not write implementation code until I ask you to proceed.
