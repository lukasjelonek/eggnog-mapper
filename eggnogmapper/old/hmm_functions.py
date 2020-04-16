##
## JHCepas
## deprecated: CPCantalapiedra 2020

def setup_hmm_search(args):
    host = 'localhost'
    idmap = None
    if args.usemem:
        scantype = 'mem'
    else:
       scantype = 'disk'

    connecting_to_server = False
    # If searching against a predefined database name
    if args.db in EGGNOG_DATABASES:
        dbpath, port = get_db_info(args.db)
        print(dbpath)
        db_present = [pexists(dbpath + "." + ext)
                      for ext in 'h3f h3i h3m h3p idmap'.split()]

        if False in db_present:
            print(db_present)
            print(colorify('Database %s not present. Use download_eggnog_database.py to fetch it' % (args.db), 'red'))
            raise ValueError('Database not found')

        if not args.no_refine:
            if not pexists(pjoin(get_data_path(), 'OG_fasta')):
                print(colorify('Database data/OG_fasta/ not present. Use download_eggnog_database.py to fetch it', 'red'))
                raise ValueError('Database not found')

        if scantype == 'mem':
            idmap_file = dbpath + '.idmap'
            end_port = 53200

    # If searching against a custom hmm database
    elif os.path.isfile(args.db + '.h3f'):
        dbpath = args.db
        if scantype == 'mem':
            idmap_file = args.db + ".idmap"
            if not pexists(idmap_file):
                if generate_idmap(args.db):
                    idmap_file = args.db + ".idmap"
                    print("idmap succesfully created!", file=sys.stderr)
                else:
                    raise ValueError("idmap could not be created!")
            port = 53000
            end_port = 53200
        else:
            idmap_file = None
            port = None

    # If searching against a emapper hmm server
    elif ":" in args.db:
        dbname, host, port = list(map(str.strip, args.db.split(":")))
        scantype = 'mem'
        port = int(port)
        if dbname in EGGNOG_DATABASES:
            dbfile, port = get_db_info(dbname)
            args.db = dbname
        else:
            dbfile = dbname

        idmap_file = dbfile + '.idmap'
        if not pexists(idmap_file):
            raise ValueError("idmap file not found: %s" % idmap_file)

        dbpath = host
        if not server_functional(host, port, args.dbtype):
            print(colorify("eggnog-mapper server not found at %s:%s" % (host, port), 'red'))
            exit(1)
        connecting_to_server = True
    else:
        raise ValueError('Invalid database name/server')


    # If memory based searches requested, start server
    if scantype == "mem" and not connecting_to_server:
        master_db, worker_db = None, None
        for try_port in range(port, end_port, 2):
            print(colorify("Loading server at localhost, port %s-%s" %
                           (try_port, try_port + 1), 'lblue'))
            dbpath, master_db, worker_db = load_server(
                dbpath, try_port, try_port + 1, args.cpu)
            port = try_port
            ready = False
            for _ in range(TIMEOUT_LOAD_SERVER):
                print("Waiting for server to become ready...", host, try_port)
                time.sleep(1)
                if not master_db.is_alive() or not worker_db.is_alive():
                    master_db.terminate()
                    master_db.join()
                    worker_db.terminate()
                    worker_db.join()
                    break
                elif server_functional(host, port, args.dbtype):
                    ready = True
                    break
            if ready:
                dbpath = host
                break
    elif scantype == "mem":
        print(colorify("DB Server already running or not needed!", 'yellow'))
        dbpath = host

    # Preload seqid map to translate hits from hmmpgmd
    if scantype == "mem":
        print(colorify("Reading idmap %s" % idmap_file, color='lblue'))
        idmap = {}
        for _lnum, _line in enumerate(open(idmap_file)):
            if not _line.strip():
                continue
            try:
                _seqid, _seqname = list(map(str, _line.strip().split(' ')))
            except ValueError:
                if _lnum == 0:
                    # idmap generated by esl_reformat has info line at beginning
                    continue  
                else:
                    raise
            _seqid = int(_seqid)
            idmap[_seqid] = [_seqname]
        print(len(idmap), "names loaded")

    # If server mode, just listen for connections and exit when interrupted
    if args.servermode:
        while True:
            print(colorify("Server ready listening at %s:%s and using %d CPU cores" % (host, port, args.cpu), 'green'))
            print(colorify("Use `emapper.py -d %s:%s:%s (...)` to search against this server" % (args.db, host, port), 'lblue'))
            time.sleep(10)
        raise EmapperException()
    else:
        return host, port, dbpath, scantype, idmap

    
def dump_hmm_matches(fasta_file, hits_file, dbpath, port, scantype, idmap, args):
    hits_header = ("#query_name", "hit", "evalue", "sum_score", "query_length",
                   "hmmfrom", "hmmto", "seqfrom", "seqto", "query_coverage")

    # Cache previous results if resuming is enabled
    VISITED = set()
    if args.resume and pexists(hits_file):
        print(colorify("Resuming previous run. Reading computed output from %s" % hits_file, 'yellow'))
        VISITED = set([line.split('\t')[0].strip()
                       for line in open(hits_file) if not line.startswith('#')])
        print(len(VISITED), 'queries skipped')
        OUT = open(hits_file, 'a')
    else:
        OUT = open(hits_file, 'w')

    print(colorify("Sequence mapping starts now!", 'green'))
    if not args.no_file_comments:
        print(get_call_info(), file=OUT)
        print('# ' + '\t'.join(hits_header), file=OUT)
    total_time = 0
    last_time = time.time()
    start_time = time.time()
    qn = 0 # in case nothing to loop bellow
    for qn, (name, elapsed, hits, querylen, seq) in enumerate(search.iter_hits(
                                                        fasta_file,
                                                        args.translate,
                                                        args.qtype,
                                                        args.dbtype,
                                                        scantype,
                                                        dbpath,
                                                        port,
                                                        evalue_thr=args.evalue,
                                                        score_thr=args.score,
                                                        qcov_thr=args.qcov,
                                                        fixed_Z=args.Z,
                                                        max_hits=args.maxhits,
                                                        skip=VISITED,
                                                        maxseqlen=args.maxseqlen,
                                                        cpus=args.cpu,
                                                        base_tempdir=args.temp_dir)):

        if elapsed == -1:
            # error occurred
            print('\t'.join(
                [name] + ['ERROR'] * (len(hits_header) - 1)), file=OUT)
        elif not hits:
            print('\t'.join([name] + ['-'] * (len(hits_header) - 1)), file=OUT)
        else:
            for hitindex, (hid, heval, hscore, hmmfrom, hmmto, sqfrom, sqto, domscore) in enumerate(hits):
                hitname = hid
                if idmap:
                    hitname = idmap[hid][0]

                print('\t'.join(map(str, [name, hitname, heval, hscore,
                                                 int(querylen), int(hmmfrom),
                                                 int(hmmto), int(sqfrom),
                                                 int(sqto),
                                                 float(sqto - sqfrom) / querylen])), file=OUT)
        OUT.flush()

        # monitoring
        total_time += time.time() - last_time
        last_time = time.time()
        if qn and (qn % 25 == 0):
            print(qn + \
                1, total_time, "%0.2f q/s" % ((float(qn + 1) / total_time)), file=sys.stderr)
            sys.stderr.flush()

    # Writes final stats
    elapsed_time = time.time() - start_time
    if not args.no_file_comments:
        print('# %d queries scanned' % (qn + 1), file=OUT)
        print('# Total time (seconds):', elapsed_time, file=OUT)
        print('# Rate:', "%0.2f q/s" % ((float(qn + 1) / elapsed_time)), file=OUT)
    OUT.close()
    print(colorify(" Processed queries:%s total_time:%s rate:%s" %\
                   (qn+1, elapsed_time, "%0.2f q/s" % ((float(qn+1) / elapsed_time))), 'lblue'))


def annotate_hmm_matches(hits_file, hits_annot_file, args):
    hits_annot_header = list(map(str.strip, '''#query_name, hit, level, evalue,
                         sum_score, query_length, hmmfrom, hmmto, seqfrom, seqto, query_coverage,
                         members_in_og, og_description, og_COG_categories'''.split(',')))

    annota.connect()
    print(colorify("Functional annotation of hits starts now", 'green'))
    start_time = time.time()
    if pexists(hits_file):
        OUT = open(hits_annot_file, "w")
        if not args.no_file_comments:
            print(get_call_info(), file=OUT)
            print('\t'.join(hits_annot_header), file=OUT)
        qn = 0
        t1 = time.time()
        for line in open(hits_file):
            if not line.strip() or line.startswith('#'):
                continue
            qn += 1
            if qn and (qn % 10000 == 0):
                total_time = time.time() - start_time
                print(qn, total_time, "%0.2f q/s (refinement)" %\
                    ((float(qn) / total_time)), file=sys.stderr)
                sys.stderr.flush()

            (query, hit, evalue, sum_score, query_length, hmmfrom, hmmto,
             seqfrom, seqto, q_coverage) = list(map(str.strip, line.split('\t')))
            if hit not in ['ERROR', '-']:
                hitname = cleanup_og_name(hit)
                level, nm, desc, cats = annota.get_og_annotations(hitname)
                print('\t'.join(map( str, [query, hitname, level, evalue,
                                                  sum_score, query_length,
                                                  hmmfrom, hmmto, seqfrom,
                                                  seqto, q_coverage, nm, desc,
                                                  cats])), file=OUT)
            else:
                print('\t'.join(
                    [query] + [hit] * (len(hits_annot_header) - 1)), file=OUT)
        elapsed_time = time.time() - t1
        if not args.no_file_comments:
            print('# %d queries scanned' % (qn), file=OUT)
            print('# Total time (seconds):', elapsed_time, file=OUT)
            print('# Rate:', "%0.2f q/s" % ((float(qn) / elapsed_time)), file=OUT)
        OUT.close()
        print(colorify(" Processed queries:%s total_time:%s rate:%s" %\
                       (qn, elapsed_time, "%0.2f q/s" % ((float(qn) / elapsed_time))), 'lblue'))


def get_seq_hmm_matches(hits_file):
    annota.connect()
    print(colorify("Reading HMM matches", 'green'))
    seq2oginfo = {}
    start_time = time.time()
    hitnames = set()
    if pexists(hits_file):
        for line in open(hits_file):
            if not line.strip() or line.startswith('#'):
                continue

            (query, hit, evalue, sum_score, query_length, hmmfrom, hmmto,
             seqfrom, seqto, q_coverage) = list(map(str.strip, line.split('\t')))

            if query not in seq2oginfo and hit not in ['ERROR', '-']:
                hitname = cleanup_og_name(hit)
                seq2oginfo[query] = [hitname, evalue, sum_score, query_length,
                                     hmmfrom, hmmto, seqfrom, seqto,
                                     q_coverage]
    return seq2oginfo

def refine_matches(fasta_file, refine_file, hits_file, args):
    refine_header = list(map(str.strip, '''#query_name, best_hit_eggNOG_ortholog,
                        best_hit_evalue, best_hit_score'''.split(',')))

    print(colorify("Hit refinement starts now", 'green'))
    start_time = time.time()
    og2level = dict([tuple(map(str.strip, line.split('\t')))
                     for line in gopen(get_oglevels_file())])
    OUT = open(refine_file, "w")

    if not args.no_file_comments:
        print(get_call_info(), file=OUT)
        print('\t'.join(refine_header), file=OUT)

    qn = 0 # in case no hits in loop bellow
    for qn, r in enumerate(process_nog_hits_file(hits_file, fasta_file, og2level,
                                                 translate=args.translate,
                                                 cpu=args.cpu,
                                                 excluded_taxa=args.excluded_taxa,
                                                 base_tempdir=args.temp_dir)):
        if qn and (qn % 25 == 0):
            total_time = time.time() - start_time
            print(qn + 1, total_time, "%0.2f q/s (refinement)" % ((float(qn + 1) / total_time)), file=sys.stderr)
            sys.stderr.flush()
        query_name = r[0]
        best_hit_name = r[1]
        if best_hit_name == '-' or best_hit_name == 'ERROR':
            continue
        best_hit_evalue = float(r[2])
        best_hit_score = float(r[3])
        print('\t'.join(map(str, (query_name, best_hit_name,
                                         best_hit_evalue, best_hit_score))), file=OUT)
        #OUT.flush()

    elapsed_time = time.time() - start_time
    if not args.no_file_comments:
        print('# %d queries scanned' % (qn + 1), file=OUT)
        print('# Total time (seconds):', elapsed_time, file=OUT)
        print('# Rate:', "%0.2f q/s" % ((float(qn + 1) / elapsed_time)), file=OUT)
    OUT.close()
    print(colorify(" Processed queries:%s total_time:%s rate:%s" %\
                   (qn+1, elapsed_time, "%0.2f q/s" % ((float(qn+1) / elapsed_time))), 'lblue'))


def process_nog_hits_file(hits_file, query_fasta, og2level, skip_queries=None,
                          translate=False, cpu=1, excluded_taxa=None, base_tempdir=None):
    sequences = {name: seq for name, seq in seqio.iter_fasta_seqs(
        query_fasta, translate=translate)}
    cmds = []
    visited_queries = set()

    if skip_queries:
        visited_queries.update(skip_queries)

    tempdir = mkdtemp(prefix='emappertmp_phmmer_', dir=base_tempdir)

    for line in gopen(hits_file):
        if line.startswith('#'):
            continue

        fields = list(map(str.strip, line.split('\t')))
        seqname = fields[0]

        if fields[1] == '-' or fields[1] == 'ERROR':
            continue

        if seqname in visited_queries:
            continue

        hitname = cleanup_og_name(fields[1])
        level = og2level[hitname]

        seq = sequences[seqname]
        visited_queries.add(seqname)
        target_fasta = os.path.join(get_fasta_path(), level, "%s.fa" % hitname)
        cmds.append([seqname, seq, target_fasta, excluded_taxa, tempdir])

    if cmds:
        pool = multiprocessing.Pool(cpu)
        for r in pool.imap(search.refine_hit, cmds):
            yield r
        pool.terminate()

    shutil.rmtree(tempdir)


def cleanup_og_name(name):
    # names in the hmm databases are sometiemes not clean eggnog OG names
    m = re.search('\w+\.((ENOG41|COG|KOG|arCOG)\w+)\.', name)
    if m:
        name = m.groups()[0]
    name = re.sub("^ENOG41", "", name)
    return name
