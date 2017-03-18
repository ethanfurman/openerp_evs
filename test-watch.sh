./cronaide watch -e ethan@stoneleaf.us -s "mail, missing parameters" ./test-cronaide
./cronaide watch -e ethan@stoneleaf.us -s "mail, bad parameters" ./test-cronaide test-watch -z
./cronaide watch -e ethan@stoneleaf.us -s "no-mail" ./test-cronaide test-watch
./cronaide watch -e ethan@stoneleaf.us -s "no-mail, plain output" ./test-cronaide test_watch -o
./cronaide watch -e ethan@stoneleaf.us -s "mail, return code seven" ./test-cronaide test_watch -r 7
./cronaide watch -e ethan@stoneleaf.us -s "no-mail, capture, no stdout" -c ./test-cronaide test_watch
./cronaide watch -e ethan@stoneleaf.us -s "mail, capture, stdout" -c ./test-cronaide test_watch -o
./cronaide watch -e ethan@stoneleaf.us -s "mail, capture, stderr" -c ./test-cronaide test_watch -e
./cronaide watch -e ethan@stoneleaf.us -s "mail, heartbeat, no stdout" -h ./test-cronaide test_watch
./cronaide watch -e ethan@stoneleaf.us -s "mail, heartbeat, stdout" -h ./test-cronaide test_watch -o
./cronaide watch -e ethan@stoneleaf.us -s "mail, heartbeat, capture, no stdout" -ch ./test-cronaide test_watch
./cronaide watch -e ethan@stoneleaf.us -s "mail, heartbeat, capture, stdout" -ch ./test-cronaide test_watch -o
./cronaide watch -e ethan@stoneleaf.us -s "no-mail, check-stdout, no stdout" ./test-cronaide test_watch
./cronaide watch -e ethan@stoneleaf.us -s "no-mail, check-stdout, stdout" ./test-cronaide test_watch -o
./cronaide watch -e ethan@stoneleaf.us -s "mail, check-stdout, ERROR in stdout" ./test-cronaide test_watch -i
./cronaide watch -e ethan@stoneleaf.us -s "mail, check-stdout, stdout, ERROR in stdout" ./test-cronaide test_watch -io
